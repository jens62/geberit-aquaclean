# Alba — Remote Control vs. Bridge Conflict

## Symptom

When the HACS integration (or standalone bridge) is polling an AquaClean Alba:

- The physical Geberit remote control shows a **yellow exclamation mark** — its
  application-layer pairing with the toilet is invalidated.
- Re-pairing the remote **fails with a red exclamation mark** while the integration
  is running.
- Re-pairing **succeeds** only after disabling the integration.
- The Geberit Home App **cannot scan for devices** while the bridge is connected —
  the device stops advertising while a BLE central is connected (standard BLE
  peripheral behaviour). At ~14 s connected per 60 s cycle the scan window can be
  missed entirely.

This is **not** a timing issue. Yellow = application-layer deregistration. Red on
re-pair = active rejection by the toilet, not a timeout.

## Why This Is Not a Simple Timing Problem

With a 60-second poll interval, the bridge connects for ~14 seconds per cycle (~23%
occupancy). The device stops advertising during that window, so the app may miss its
scan. Between polls the device is free — but the toilet actively rejects the remote
even then. The remote's `WrongPairingSecret`-style rejection is an application-layer
decision, not a BLE-layer busy signal.

## Root Cause — PARTIALLY IDENTIFIED, INCOMPLETE (2026-05-28)

### What was fixed in v3.0.3 (necessary but not sufficient)

The Geberit Home App sends two additional Ble20 protocol initialisation commands
after `DataPointInventory` and before starting DpId reads:

1. **`CapabilitiesCmd` (0xFD)** — "what capabilities does this device have?"
2. **`EventStorageInventory` (0x50)** — "what events are stored?"

The bridge (before v3.0.2) sent only `DataPointInventory` and then immediately
started DpId reads. v3.0.3 fixed this — caps+event_storage now sent on every poll.
Confirmed from MuusLee's v3.0.3 log: every poll shows `→ fd`, `← fe02`, `→ 50`,
event storage drain, then DpId reads.

**However: displacement still persists in v3.0.3** (MuusLee confirmed 2026-05-28).
After deactivating the integration the device still needs a toilet restart before
the remote and app can reconnect — persistent state corruption.

v3.0.2 sent the two commands only when running a fresh DataPointInventory (first
poll). Subsequent polls reused the coordinator inventory cache and skipped both
commands — the device saw a fresh BLE connection with only DpId reads and treated
the bridge as an unrecognised client, displacing the remote on every poll after
the first. Confirmed from MuusLee's v3.0.2 HA log (2026-05-27).

### EventStorageInventory is not a "consume and clear" queue (2026-05-28)

Device returns the same 2 event frames on every poll within a session group:
- `← 520800310010` / `← 520000700008` (stable across polls 1–3)
- One byte increments between fresh inventory sessions (not per poll)

Events are NOT consumed by being read — the remote's event data is unaffected.

### Current behavioral facts

- Bridge v3.0.3 (keyset 0, KE completes, caps+event_storage, DpId reads) → **displaces remote**
- Bridge v3.0.4b1 (keyset 1, KE never completes, zero DpId reads) → **displaces remote**
- Wrong-PIN app (keyset 0, KE completes, zero DpId reads) → **does NOT displace**
- Registered app (keyset 0, KE completes, caps+event_storage, DpId reads) → **does NOT displace**
- Bridge Ble20 init sequence is now **identical** to app at Ble20 level (v3.0.3)

The v3.0.4b1 accidental test proves displacement is not caused by caps/event_storage/DpId
reads — it occurred with zero successful Ble20 transactions. The KE Request step itself
(specifically using keyset_id=1) is a candidate; however v3.0.3 (keyset_id=0) also displaces.
Root cause unknown.

## What Was Ruled Out

| Candidate | Verdict |
|-----------|---------|
| BLE timing (bridge occupies device continuously) | ❌ Device free 55 s per 60 s cycle |
| Arendi KE handshake alone (keyset 0) | ❌ Wrong-PIN test: KE completes, no DpId reads → remote NOT displaced (MuusLee, 2026-05-27) |
| DpId reads with successful app session | ❌ Registered app reads DpIds AND coexists with remote (MuusLee, 2026-05-27) |
| `DP_JOIN_DEVICE` as registration fix | ❌ **Invalidated (2026-05-27):** DpId 543 absent from 78-DpId inventory on Alba 250. Bridge v3.0.1b1 skipped JOIN on every poll; conflict persists unchanged. |
| `DP_JOIN_DEVICE` on every poll (b23) | ❌ Not the cause: removed in v3.0.0, conflict persists |
| `DP_PAIRING_MODE (356)` writable | ❌ Read-only Status field |
| `AC_CMD_ACTIVATE_USER_SESSION (65586)` called | ❌ Not called anywhere |
| `DP_END_USER_SESSION` as a release mechanism | ❌ Does not exist in the protocol |
| Bridge sending wrong bytes to DP_RESTART | ✅ Fixed (d88aba0) — unrelated to deregistration |
| Bridge polling write-only DpId 563 | ✅ Fixed (d88aba0) — unrelated to deregistration |
| `DP_START_USER_SESSION (802)` | ❌ **Ruled out (2026-05-28)** — pcapng comparison shows zero WriteCmds in any app init session; all app→device frames after EventStorageInventory are ReadCmds only |

## Fix Options

| Option | Status |
|--------|--------|
| `CapabilitiesCmd` + `EventStorageInventory` on every fresh BLE connection | ✅ **IMPLEMENTED (v3.0.3)** — necessary but NOT sufficient; displacement persists |
| `DP_JOIN_DEVICE` once with PIN | ❌ **Invalidated** — DpId 543 absent from inventory on Alba 250 |
| `DP_START_USER_SESSION` (DpId 802) write after KE | ❌ **Ruled out** — not observed in any app session |
| keyset_id=1 in KE Request | ❌ **Ruled out (2026-05-28)** — app confirmed keyset_id=0x00; keyset 1 is the remote's key |
| BLE notification mode — connect once, stay connected | Open — would remove the repeated-connect problem but adds complexity |

## Investigation Steps — Cheapest First

Before resorting to hardware sniffing, two software-only tests narrow the cause:

### Step 0 — Does v3.0.0 already fix the conflict?

**Result (MuusLee, 2026-05-27): ❌ Conflict persists.** Yellow exclamation mark still
appears after poll cycles with v3.0.0 (tested with and without `alba_pin` configured).
`DP_JOIN_DEVICE` is not the cause. Proceed to Step 1.

### Step 1 — Wrong-PIN test (no hardware required)

**Result (MuusLee, 2026-05-27): ❌ Remote NOT displaced.**
Fresh phone, wrong PIN entered → app failed with wrong-PIN error → remote showed
no yellow exclamation mark afterward.

Since the Arendi KE handshake completes successfully regardless of the PIN outcome,
this rules out KE alone as the cause.

**How the PIN prompt appears (confirmed from protocol analysis):** the app attempts
`DP_JOIN_DEVICE` instance 0 (no PIN) immediately after KE. The device responds with
`JoinErrorStatus.Protected`. Only then does the app display the PIN prompt. No DpId
reads occur anywhere in this path — the prompt appears within ~2 seconds of the app
finding the device. The app flow is: KE → JOIN(no PIN) → Protected → PIN prompt →
JOIN(wrong PIN) → WrongPairingSecret → error, with zero DpId reads. This closes the
open question and validates the comparison table below.

### Step 2 — App + remote coexistence test

**Result (MuusLee, 2026-05-27): ❌ App does NOT displace remote.**
MuusLee confirmed the Geberit Home App and the physical remote can be used
simultaneously — both work normally. The app uses keyset 0 (identical to the bridge)
and performs a full DpId read cycle after KE, yet the remote remains functional.

This breaks the earlier "DpId reads are the trigger" conclusion. The bridge and the
registered app both do DpId reads; only the bridge displaces the remote.

### Evidence Summary (2026-05-27)

| Scenario | KE | DpId reads | JOIN available? | Remote displaced? |
|---|---|---|---|---|
| v3.0.1b1 bridge | ✓ keyset 0 | ✓ full read cycle | ✗ not in inventory | **Yes** |
| Wrong-PIN app (fresh phone) | ✓ keyset 0 | ✗ none | unknown | **No** |
| Geberit Home App (registered) | ✓ keyset 0 | ✓ full read cycle | unknown | **No** |

`DP_JOIN_DEVICE` (DpId 543) is absent from the 78-DpId inventory on Alba 250.
The bridge's v3.0.1b1 JOIN fix silently skipped on every poll.
Whatever the app does differently from the bridge, it is not `DP_JOIN_DEVICE`.

**The "JOIN" column in the earlier version of this table was wrong** — it assumed the
app's registration mechanism was `DP_JOIN_DEVICE`, which is not supported on this device.
The actual distinguishing factor is still unknown.

### Step 3 — Analyse kstr pcapng (no new hardware required)

**Status: DONE (2026-05-27) — root cause confirmed.**

`local-assets/Android-BLE-Logs/kstr/GeberitConnect4xViaApp.pcapng` (4 sessions, same
firmware `RS03TS89 / 1.14.1 1.2.0`) was parsed with a COBS+HDLC decoder.

**Finding:** all 4 fresh-connection sessions show an identical 2-frame pattern between
the last `InventoryData` frame and the first `ReadCmd` DpId read:

| Frame | Ciphertext size | Ble20 payload | Identity |
|-------|----------------|---------------|----------|
| ns=4  | 6 B (1 B plain) | `[0xFD]`     | `CapabilitiesCmd` |
| ns=5  | 6 B (1 B plain) | `[0x50]`     | `EventStorageInventory` |

Device responds to `CapabilitiesCmd` with a `CapabilitiesAck` (0xFE) + 1 B flags.
Device responds to `EventStorageInventory` with `EventStorageInventoryCount` (0x51) +
2 B count, then N × `EventStorageInventoryData` (0x52) frames.

Reconnect sessions (where the app skips inventory due to firmware-version cache match)
also skip both of these frames — confirming the "skip on reconnect" pattern.

### Step 4 — Pcapng vs bridge frame comparison (2026-05-28)

**Status: DONE — no Ble20-level difference found.**

`GeberitConnect4xViaApp.pcapng` decoded with `tools/android-ble-arendi-decode.py` and
compared frame-by-frame against MuusLee's v3.0.2 HA DEBUG log (poll 1).

**App session 1 — app→device sequence:**

| Step | Ciphertext | Ble20 payload |
|------|-----------|---------------|
| DataPointInventory | ENC 7B → 2B | `[0x00, 0x00]` |
| CapabilitiesCmd | ENC 6B → 1B | `[0xFD]` |
| EventStorageInventory | ENC 6B → 1B | `[0x50]` |
| ReadCmds × ~18 | ENC 8B → 3B each | `[0x10, lo, hi]` |

**Findings:**
- The app sends **no WriteCmds** (`0x20`) in its fresh-connection init session — there
  is no "write DpId X to register" step. This rules out `DP_START_USER_SESSION` and any
  other write-based registration mechanism.
- No extra command exists between EventStorageInventory and the first ReadCmd.
- The v3.0.3 bridge sequence is **identical** to the app's at the Ble20 level.

**What the pcapng comparison cannot see:**

1. **KE Request bytes** — the 56-byte KE Request frame is split across 3 ATT writes.
   The pcapng decode tool processes each ATT write independently and cannot reassemble
   multi-write frames, so `ns=2` (SEC_KE_REQ) never appears in the output.
   `keyset_id` (last byte of write 3) CAN be extracted from raw ATT write bytes and
   was confirmed as `0x00` for the Android app (see Keyset Analysis section below).

2. **Which DpIds are read** — encrypted; both sides produce identical 8B → 3B frames.

**Conclusion:** the comparison gives no evidence that v3.0.3 is missing anything at the
Ble20 command level. keyset_id is identical (both 0x00). No remaining KE Request
difference has been identified.

### Step 5 — v3.0.3 hardware test result (2026-05-28)

**Status: DONE — displacement still occurs.**

MuusLee confirmed v3.0.3 result: remote shows yellow exclamation mark during
polling. After deactivating the integration, toilet restart required before remote
and app can reconnect. The caps+event_storage fix was necessary but not sufficient.

The v3.0.4b2 pre-release adds `logger.debug` of the full KE Request hex (50 bytes)
to `AriendiSecurity.py`. With HA debug logging enabled this will appear as:
```
AriendiSecurity: KE Request → 12<32B pubkey><16B cmac>00
```
This confirms keyset_id=0x00 in MuusLee's log and preserves the bytes for
comparison against a future PCA10059 capture.

### Step 6 — v3.0.4b1 accidental keyset_id=1 test (2026-05-28)

**Status: DONE — displacement without any successful Ble20 transaction.**

v3.0.4b1 contained a bug: `keyset_id=0x01` instead of `0x00` in the KE Request.
MuusLee tested this version. Full log: `home-assistant_2026-05-28T11-28-00.621Z.log`.

**What happened (7 consecutive attempts, 13:14–13:27):**
- Each attempt: SABM → UA → Version Request/Response → EP Request/Response → KE Request
- Device responded normally through EP Response (keyset_mask=0x0003 confirmed)
- On each KE Request (keyset_id=0x01, invalid CMAC for that keyset): **device silent** — no KE Response
- Bridge timed out after 5 s → E0003, disconnected
- **Remote was displaced on the first attempt** — yellow exclamation mark appeared despite
  zero caps/event_storage/DpId reads and no completed KE handshake

**Static EP nonces — confirmed protocol property:**
The device returned the **same** nonce pair on all 7 reconnections across 13 minutes:
```
nonce1=7d23517ec89958df0567aeb7f722a024 nonce2=b58fc768522fb8336c6131967548d9a0
```
The device does NOT regenerate nonces between BLE connections. Nonces are only
refreshed after a valid KE completes. This is consistent with the device maintaining
a "pending session" epoch until one client completes authentication.

**What this narrows:**
Displacement is not triggered by caps/event_storage/DpId reads. It is triggered at
or before the KE step. The KE Request with keyset_id=1 is the most likely immediate
cause — the device may "reserve" the keyset-1 slot on receipt of a KE Request claiming
that keyset, blocking the physical remote (the real keyset-1 holder) even when the CMAC
is invalid. However, this does not explain why v3.0.3 (keyset_id=0) also displaces.

**Recovery required:** toilet power-cycle. Disabling the integration alone was not
sufficient — the device needed a restart before the remote (and app) could reconnect.

**Updated evidence table:**

| Scenario | KE | DpId reads | Remote displaced? |
|---|---|---|---|
| v3.0.3 bridge | ✓ keyset 0 completes | ✓ full read cycle | **Yes** |
| v3.0.4b1 bridge | ✗ keyset 1, KE never completes | ✗ none | **Yes** |
| Wrong-PIN app | ✓ keyset 0 completes | ✗ none | **No** |
| Geberit Home App (registered) | ✓ keyset 0 completes | ✓ full read cycle | **No** |

---

## Investigation Plan — PCA10059 BLE Sniff

The PCA10059 (nRF52840 dongle) flashed with Nordic sniffer firmware + Wireshark
is the right tool. Alba does not use BLE SM encryption (zero SMP frames in all
captures), so all ATT write payloads — including the raw Arendi KE frames — are
visible in plaintext.

**Setup guide:** `docs/developer/ble-traffic-capture.md` → section
"nRF52840 Dongle — Passive BLE Sniffer". Step-by-step flash + Wireshark plugin
install + tshark extraction + KE Request decode script.

### Captures needed

**A. Official app session (baseline)**
- Open Geberit app → connect → complete init
- Establishes the app's KE Request as ground truth (keyset_id=0x00 already confirmed
  from two independent sources; sniffer gives a third independent confirmation)

**B. Bridge poll cycle**
- Trigger one HACS poll
- Compare bridge KE Request byte-for-byte against the app capture

**C. Remote control**
- Press a button on the physical remote (it BLE-connects momentarily)
- **Only way to see the remote's KE Request** — confirms keyset_id=0x01 and reveals
  whether any other byte differs from app/bridge frames

**D. Displacement in action**
- Remote in normal (no exclamation) state → trigger bridge poll → observe
- Key timing question: does the toilet notify the remote **before** bridge KE completes,
  or after? This locates the displacement trigger at EP level vs KE level

### Key question the sniff will answer

What does the toilet send to the remote (or what does the remote see) at the moment
of displacement? If the toilet sends an explicit Arendi-layer rejection to the remote
during the bridge's EP or KE exchange, the mechanism is in that layer. If there is no
BLE traffic to the remote at all (remote just silently loses its pairing state), the
displacement is entirely device-internal with no observable BLE signal.

## What the KE Request Is and Why Available Sources Don't Reveal the Root Cause

### The Arendi handshake — five steps before any data

Before any DpId read or write, the client runs a five-step handshake:

```
1. SABM / UA          — link-layer connect (like TCP SYN/ACK)
2. Version            — negotiate protocol version
3. EP Request/Response — device sends two random nonces + keyset_mask
4. KE Request/Response — Diffie-Hellman key exchange, derives session keys
5. Encrypted session  — all Ble20 frames (DpId reads etc.) AES-CTR encrypted
```

The KE Request (step 4) is a 50-byte frame:

| Offset | Length | Content |
|--------|--------|---------|
| 0 | 1 | Type byte `0x12` |
| 1 | 32 | Curve25519 ephemeral public key (fresh random per session) |
| 33 | 16 | CMAC over the public key, keyed from the pre-shared secret |
| 49 | 1 | `keyset_id` — which credential slot to authenticate against |

The device verifies the CMAC, completes its half of the DH exchange, and sends a
KE Response. Both sides then independently derive identical session keys. Everything
from step 5 onward is encrypted.

### Why `btsnoop_hci.log` cannot reveal the root cause

`btsnoop_hci.log` is MuusLee's Android phone's HCI log — it captures BLE traffic
that passes through the phone's own Bluetooth chip. It shows only what the **phone**
sends and receives when it connects to the toilet.

It cannot show:
- What the **bridge** sends — the bridge connects directly from the HA host's BLE adapter
- What the **remote** sends — the remote connects directly to the toilet, never through the phone

To compare bridge, app, and remote KE Requests side by side, a PCA10059 sniffer
placed between all devices and the toilet is required — not any single device's HCI log.

**What the btsnoop CAN confirm:** that MuusLee's phone sends keyset_id=0x00 (see below).

### Why the decompiled app source cannot reveal the root cause

The decompiled source (`Security.cs`) shows the algorithm the app uses to build the
KE Request — Curve25519 key generation, HKDF-SHA256 key derivation, AES-CMAC
authentication. Our bridge's `AriendiSecurity.py` is a faithful Python implementation
of the same algorithm. Reading the source confirms the app does the same thing the
bridge does, but gives no insight into why the **device** responds differently.

The displacement decision — "invalidate the remote's keyset-1 session when a
keyset-0 client connects" — is made inside the **toilet's firmware**, which is not
available. The app source is the sender side; the side that matters is the receiver.

---

## Keyset Analysis — Android BLE Log (kstr, 2026-05-26) + MuusLee btsnoop (2026-05-31)

### kstr — GeberitConnect4xViaApp.pcapng

The `GeberitConnect4xViaApp.pcapng` capture from the kstr device was COBS-decoded to
extract the raw Arendi protocol fields.

**EP Response (device → app):**
- keyset_mask bytes 33–34 = `0x03 0x00` → **keyset_mask = 0x0003**
- Bit 0 set = keyset 0 supported; Bit 1 set = keyset 1 supported
- Device advertises **two keysets**: 0 and 1

**KE Request (app → device):** keyset_id = **0x00**

### MuusLee — btsnoop_hci.log (2026-05-31)

MuusLee's Android HCI log was decoded directly. The KE Request spans 3 ATT Write
Command packets (frames 861–863, handle 0x001e). COBS+HDLC decode + CRC16 verification:

```
Concatenated raw  : 003344...035a0f00  (56 bytes)
COBS-decoded      : 44 12 1cd90958...64d000  (53 bytes, CRC ✓)
HDLC ctrl         : 0x44 → I-frame N(S)=2 N(R)=2
KE Request payload: 50 bytes
  type      = 0x12
  pubkey    = 1cd90958f1b7192db385592de39e82bc674daf615c03e4c98d9acca0fafb7140
  cmac      = 456a8958bb5bb4285f6188c5731d64d0
  keyset_id = 0x00  ← confirmed
```

**Both MuusLee and kstr send keyset_id=0x00.** The bridge also sends keyset_id=0x00
(confirmed from v3.0.4b1 hex logging and v3.0.4b2 code). The keyset_id is identical
across all three clients — it is not the differentiator.

### What keyset 1 is

Keyset 0 is the shared app/bridge key (`aquacleanBridgeId`). Keyset 1 is the physical
remote control's device-specific key, established during NFC pairing. The remote uses
its own key as HKDF IKM; the device validates it against the keyset-1 slot.

### PIN hypothesis — disproven

The PIN is not used in the KE handshake. PIN entry happens at the application layer
(DpId write after encryption is established), not in the KE exchange.

**Keyset ownership question — still open:**
The app+remote coexistence test rules out a hard keyset-0 single-owner model — but
does not explain why the bridge (also keyset 0) displaces the remote when the app does not.

## PIN and DP_JOIN_DEVICE — CONFIRMED NOT RELEVANT (2026-05-27)

### DP_JOIN_DEVICE is a wired gateway command

`DP_JOIN_DEVICE` (DpId 543) description from vendor source:

> *"Join a device to the gateway."*

This is a **GeBus gateway topology command** — it registers a device into a Geberit
Home hub's network. The `GEBUS_DISTURBANCE` error flag in `DP_JOIN_DEVICE_ERROR`,
`GEBUS Station` and `Idc Address` fields in the device list, and references to
`"Wireless joined device on GEBUS"` all confirm the GeBus context. GeBus supports
both wired and wireless members; the physical layer is not confirmed from app source
alone.

**It has nothing to do with a BLE phone or bridge connecting directly to a toilet.**
`Ble20Product.Initialize()` — the full Alba BLE session init — never calls `Join`,
never reads `DP_PAIRING_SECRET`, and never references `JoinUtil`. Confirmed from
decompiled vendor app source. The 4-session kstr pcapng confirms: no DpId 12 read,
no JOIN write anywhere in any session.

### Mera Comfort has NO PIN

The AquaClean Mera Comfort has no PIN at all — no sticker, no `DP_PAIRING_SECRET`,
no PIN prompt in the Geberit Home App.

### Alba PIN (`DP_PAIRING_SECRET`, DpId 12)

Alba has a 4-digit PIN printed on the toilet sticker, stored as `DP_PAIRING_SECRET`
(DpId 12). The Geberit Home App prompts for it on first connect. However, this PIN
is for the GeBus gateway joining mechanism only — it is not used in the Ble20 BLE
wire protocol for direct app-to-toilet connections.

### `alba_pin` config field — inert, should be removed

The `alba_pin` config field was based on the false assumption that `DP_JOIN_DEVICE`
was an app authentication mechanism. It never reaches the wire on any known device.
The `_maybe_join()` scaffolding is harmless (DpId 543 absent from inventory →
silently skipped), but the field should be removed from the UI.

## DP_JOIN_DEVICE — WRONG HYPOTHESIS (FULLY RULED OUT)

> **2026-05-27:** `DP_JOIN_DEVICE` (DpId 543) is absent from the 78-DpId inventory
> on Alba 250. But more fundamentally: `DP_JOIN_DEVICE` is a **wired GeBus gateway**
> topology command ("Join a device to the gateway") — it has nothing to do with a
> BLE app or bridge connecting directly to a toilet. The hypothesis was wrong at
> the protocol level, not just because DpId 543 was absent.

The `join()` scaffolding in `Ble20Client.py` and the `alba_pin` config field are
inert. `alba_pin` should be removed from the config flow UI.


## Related Files

- `aquaclean_console_app/bluetooth_le/LE/AriendiSecurity.py` — `aquacleanBridgeId`, KE Request
- `aquaclean_console_app/aquaclean_core/Clients/AlbaClient.py` — poll path (does NOT call `start_user_session`)
- `docs/developer/alba-ble-encryption.md` — Arendi protocol full analysis
- `docs/developer/ble-traffic-capture.md` — how to use PCA10059 with Wireshark
