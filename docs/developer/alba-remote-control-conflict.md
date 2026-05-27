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

## Root Cause — CONFIRMED (2026-05-27)

The Geberit Home App sends two additional Ble20 protocol initialisation commands
after `DataPointInventory` and before starting DpId reads:

1. **`CapabilitiesCmd` (0xFD)** — "what capabilities does this device have?"
2. **`EventStorageInventory` (0x50)** — "what events are stored?"

The bridge (before v3.0.2) sent only `DataPointInventory` and then immediately
started DpId reads. By skipping `CapabilitiesCmd` and `EventStorageInventory`,
the bridge was identified as a non-standard (non-app) client, causing it to displace
the physical remote.

**Evidence chain:**
- All fresh app connections: `Inventory` → `CapabilitiesCmd` → `EventStorageInventory` → DpId reads → remote **NOT** displaced
- All bridge connections (pre-fix): `Inventory` → DpId reads — remote **DISPLACED**
- App reconnects (cached inventory): no inventory, no capabilities, no event storage → just DpId reads → remote **NOT** displaced (device already recognised app from prior session)
- Confirmed from decompiled vendor app source (`Ble20Product.cs`, `Initialize()` method)
- Confirmed from kstr `GeberitConnectViaApp.pcapng`: all 4 sessions show identical 2-frame post-inventory sequence before first DpId read

**Fix (v3.0.3):** `AlbaClient.post_connect()` now calls `capabilities()` then
`event_storage_inventory()` on **every** fresh BLE connection, regardless of
whether the DataPointInventory is taken from cache. Only the slow 12-second
DataPointInventory download is skipped when a cached inventory is available.

v3.0.2 sent the two commands only when running a fresh DataPointInventory (first
poll). Subsequent polls reused the coordinator inventory cache and skipped both
commands — the device saw a fresh BLE connection with only DpId reads and treated
the bridge as an unrecognised client, displacing the remote on every poll after
the first. Confirmed from MuusLee's v3.0.2 HA log (2026-05-27).

The behavioral facts remain established:

- Bridge (keyset 0, KE, DpId reads, no extra writes) → **displaces remote**
- Wrong-PIN app (keyset 0, KE, no DpId reads, failed join attempt) → **does NOT displace**
- Registered app (keyset 0, KE, DpId reads, caps + event storage) → **does NOT displace**

The `DP_START_USER_SESSION` hypothesis is now ruled out.

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
| `DP_START_USER_SESSION (802)` | ⚠️ **Open** — bridge does NOT call this; app may call it. Needs pcapng analysis. |

## Fix Options

| Option | Status |
|--------|--------|
| `CapabilitiesCmd` + `EventStorageInventory` on every fresh BLE connection | ✅ **IMPLEMENTED (v3.0.3)** — root cause confirmed, fix deployed |
| `DP_JOIN_DEVICE` once with PIN | ❌ **Invalidated** — DpId 543 absent from inventory on Alba 250 |
| `DP_START_USER_SESSION` (DpId 802) write after KE | ❌ **Ruled out** — not observed in any app session |
| BLE notification mode — connect once, stay connected | Deferred — root cause now fixed |

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

---

## Investigation Plan — PCA10059 BLE Sniff

The PCA10059 (nRF52840 dongle) flashed with Nordic sniffer firmware + Wireshark
is the right tool. Alba does not use BLE SM encryption (zero SMP frames in all
captures), so all ATT write payloads — including the raw Arendi KE frames — are
visible in plaintext.

### Captures needed

**1. Remote normal operation (no bridge)**
- What to do: let the remote connect to the toilet, use it normally, disconnect
- What to look for: full Arendi KE Request payload — specifically the client ID
  embedded in the CMAC computation

**2. Bridge poll cycle**
- What to do: let the bridge do one complete poll (connect → reads → disconnect)
- What to look for: bridge's KE Request payload — compare client ID against remote's

**3. Remote re-pair with integration running → red exclamation**
- What to do: trigger remote re-pairing while bridge is actively polling
- What to look for: what does the toilet send back as rejection? An explicit error
  frame in the Arendi layer, or a BLE-level rejection?

**4. Remote re-pair with integration disabled → success**
- What to do: disable integration, trigger re-pairing
- What to look for: what succeeds in the KE exchange that failed in capture 3?

### Key question the sniff will answer

Do the bridge and remote use different client IDs in their KE Requests? If yes,
and the toilet stores only one authorised ID, that confirms the single-owner model
and defines the fix space.

## Keyset Analysis — Android BLE Log (kstr, 2026-05-26)

The `GeberitConnectViaApp.pcapng` capture from the kstr device was COBS-decoded to
extract the raw Arendi protocol fields.

**EP Response (device → app):**
- keyset_mask bytes 33–34 = `0x03 0x00` → **keyset_mask = 0x0003**
- Bit 0 set = keyset 0 supported; Bit 1 set = keyset 1 supported
- Device advertises **two keysets**: 0 and 1

**KE Request (app → device):**
- keyset_id byte (final byte before CRC) = **0x00**
- The official Geberit Android app sends **keyset 0** (`aquacleanBridgeId`)
- This is identical to the keyset the bridge uses

**What keyset 1 is:**
Keyset 0 is the shared app/bridge key (`aquacleanBridgeId`). Keyset 1 is almost
certainly the physical remote control's device-specific key, established during
physical pairing (NFC touch). The remote uses its own key as HKDF IKM; the device
validates it against the keyset-1 slot.

**PIN hypothesis — disproven at the Arendi KE layer:**
The PIN is not used to select or derive a keyset in the Arendi KE handshake. The
Geberit app uses keyset 0 whether or not a PIN is set on the device. PIN entry happens
at the application layer (written via a DpId write after encryption is established),
not in the KE handshake. Using the PIN as HKDF IKM would require implementing
keyset 1 — and knowing the keyset-1 IKM, which is device-specific and not derivable
from the PIN.

**Keyset ownership question — still open:**
The per-keyset vs. global ownership question was considered resolved by the JOIN
hypothesis. With JOIN invalidated, the keyset question is open again. The app+remote
coexistence test shows two keyset-0 clients can coexist, which rules out a hard
keyset-0 single-owner model — but does not explain why the bridge (also keyset 0)
still displaces the remote.

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
