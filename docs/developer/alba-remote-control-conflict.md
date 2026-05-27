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

## Root Cause — Registered vs. Unregistered Clients

> **Updated 2026-05-27 (revised):** App + remote confirmed to coexist (MuusLee).
> The root cause is not DpId reads per se — it is the absence of a successful
> `DP_JOIN_DEVICE` registration. See "Confirmed Root Cause" under Investigation Steps.

The device maintains a **client registry**. Clients that have successfully called
`DP_JOIN_DEVICE` (with the correct PIN) are *registered*. Registered clients coexist:
the Geberit Home App and the physical remote can be used simultaneously without either
displacing the other.

The bridge (v3.0.0) removed `DP_JOIN_DEVICE` from its poll path. It connects, performs
the Arendi KE handshake, reads DpIds, and disconnects — without ever registering.
An unregistered client that successfully reads DpIds claims the device's "unregistered
session slot." When the remote subsequently connects, it finds its registration
displaced by the anonymous bridge session → red exclamation on re-pair.

The physical remote registers via NFC touch (local action, no PIN). The Geberit Home
App registers via `DP_JOIN_DEVICE` instance 1 (with PIN). Both are then in the
registry and coexist. The bridge is in neither — it is the only client that reads
DpIds without holding a registry entry.

**Fix: re-enable `DP_JOIN_DEVICE` with the user's PIN, once, on first connect.**
The `alba_pin` HACS config field already exists for this purpose. Once registered,
the bridge behaves like a second phone — it coexists with the remote and the app
without displacing either.

## What Was Ruled Out

| Candidate | Verdict |
|-----------|---------|
| `DP_START_USER_SESSION (802)` written by bridge | ❌ Bridge does NOT call this in the poll path |
| `DP_END_USER_SESSION` as a release mechanism | ❌ Does not exist in the protocol |
| `DP_PAIRING_MODE (356)` writable | ❌ Read-only Status field |
| `AC_CMD_ACTIVATE_USER_SESSION (65586)` called | ❌ Not called anywhere |
| BLE timing (bridge occupies device continuously) | ❌ Device free 55 s per 60 s cycle |
| Bridge sending wrong bytes to DP_RESTART | ✅ Fixed (d88aba0) — was causing restart to fail, unrelated to deregistration |
| Bridge polling write-only DpId 563 | ✅ Fixed (d88aba0) — was wasteful, unrelated to deregistration |
| `DP_JOIN_DEVICE` on every poll | ❌ Not the cause: removed in v3.0.0, conflict persists (MuusLee, 2026-05-27) |
| Arendi KE handshake alone (keyset 0) | ❌ Ruled out: wrong-PIN test — KE completes but remote not displaced when no DpId reads follow (MuusLee, 2026-05-27) |
| DpId reads alone (without JOIN) | ✅ **Confirmed cause**: bridge reads DpIds without a registry entry → displaces remote |
| DpId reads with successful JOIN | ❌ Not the cause: Geberit app reads DpIds AND coexists with remote after successful JOIN (MuusLee, 2026-05-27) |

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

### Confirmed Root Cause — Absence of JOIN Registration

Combining all three steps:

| Scenario | KE | DpId reads | JOIN | Remote displaced? |
|---|---|---|---|---|
| v3.0.0 bridge (normal poll) | ✓ keyset 0 | ✓ full read cycle | ✗ removed | **Yes** |
| Wrong-PIN app (fresh phone) | ✓ keyset 0 | ✗ none (JOIN before reads) | ✗ failed | **No** |
| Geberit Home App (registered) | ✓ keyset 0 | ✓ full read cycle | ✓ succeeded | **No** |

The bridge and the registered app share the same keyset and the same DpId reads. The
only difference: the app has a successful `DP_JOIN_DEVICE` entry in the device's client
registry; the bridge does not.

**Confirmed root cause:** an unregistered client (no JOIN) that reads DpIds claims the
device's unregistered session slot and displaces the remote. A registered client (JOIN
succeeded) coexists with the remote regardless of DpId reads.

### Step 3 — PCA10059 BLE Sniff (optional, for confirmation only)

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

**Keyset ownership question — superseded:**
The per-keyset vs. global ownership question is no longer the critical unknown. The
app+remote coexistence test (Step 2) shows that two keyset-0 clients (app + bridge) can
coexist once both are registered via JOIN. The displacement is caused by the absence of
registration, not by keyset collision. A PCA10059 sniff is no longer required to unblock
the fix.

## PIN Mechanism — DP_PAIRING_SECRET and DP_JOIN_DEVICE

### What the PIN is

The 4-digit PIN printed on the toilet sticker is `DP_PAIRING_SECRET` (DpId 12).
It is a 4-byte string stored on the device with a default value of `"8645"`.
Its purpose, as described in the Geberit protocol: *"Control number for pairing
without local action."*

"Local action" means physically pressing a button on the toilet or touching NFC.
The PIN allows an app or bridge to register as a paired client **without** physical
presence. The physical remote control pairs via NFC (= local action) and therefore
never needs the PIN.

When a device has a non-default PIN configured it is considered **Protected**. Any
JOIN attempt without the correct PIN is rejected with `JoinErrorStatus.WrongPairingSecret`.

**Every Alba in the field is Protected.** Users are required to enter the printed
PIN when connecting the Geberit Home App for the first time. This means every
shipped device carries a unique per-device PIN — the protocol-spec default `"8645"`
is not used in production devices.

**The PIN is required on first connect only.** Subsequent app connections do not
prompt for it. This confirms the PIN is used exclusively in `DP_JOIN_DEVICE`
(the initial registration step) and plays no role in the Arendi KE handshake on
ongoing connections.

### DP_JOIN_DEVICE payload variants

`DP_JOIN_DEVICE` (DpId 543) has three instance variants:

| Instance | Payload | Use case |
|----------|---------|----------|
| 0 | `[Series 1B][Variant 1B][UniqueID 4B]` | Join on unprotected device (no PIN required) |
| 1 | `[Series 1B][Variant 1B][UniqueID 4B][PairingSecret 4B]` | Join on Protected device with PIN |
| 2 | `[Series 1B][Variant 1B][UniqueID 4B][PairingSecret 4B][Zone 1B]` | Join with zone assignment |

The `JoinErrorStatus` flags relevant to the bridge:

| Flag | Bit | Meaning |
|------|-----|---------|
| `Protected` | 1 | Device has non-default PIN; retry with instance 1 |
| `WrongPairingSecret` | 2 | PIN was provided but incorrect |
| `TooManyDevices` | 3 | Device's client registry is full |

`TooManyDevices` confirms the device maintains a **finite-size client registry**.
The eviction policy (FIFO, LRU, or explicit remove) is unknown.

### PIN is not used in Arendi KE

The PIN (`DP_PAIRING_SECRET`) has no role in the Arendi security handshake.
The KE layer uses a fixed pre-shared key (`aquacleanBridgeId`) as keyset 0,
confirmed by the Android BLE log (keyset_id = `0x00` in every KE Request,
regardless of whether the device has a custom PIN set). The Geberit app transmits
the PIN at the application layer — as part of the DP_JOIN_DEVICE write payload,
after encryption is already established — not inside the KE handshake.

### How remote pairing differs from app/bridge pairing

| Client | Pairing method | PIN required |
|--------|---------------|-------------|
| Physical remote | NFC touch (local action) | No |
| Geberit Home App | DP_JOIN_DEVICE instance 1 | Yes (if Protected) |
| Bridge (b23+) | DP_JOIN_DEVICE instance 0/1 | Only if Protected |

The remote's NFC pairing likely writes directly to a dedicated registration slot
on the device, separate from the DP_JOIN_DEVICE application-layer registry. Whether
these share the same finite pool (and thus compete for slots) is unknown without
a PCA10059 sniff of the NFC pairing exchange.

## DP_JOIN_DEVICE — Fix Path

The fix is to re-enable `DP_JOIN_DEVICE` with the user's PIN, **once on first connect**,
not on every poll. Once the bridge holds a registry entry it behaves like a second
phone — subsequent polls skip JOIN and coexist with the remote and app.

**Implementation (already scaffolded):**
- `join()` method exists in `Ble20Client.py`
- `alba_pin` config field exists in HACS and the standalone bridge
- Call JOIN once in `post_connect()` guarded by a "already registered" flag
- On `TooManyDevices` error: log a warning; the bridge cannot register but polling
  still works (with the displacement side-effect until a slot frees up)
- On `WrongPairingSecret`: surface a clear config error to the user

**Why every-poll JOIN was wrong (b23 behaviour):** calling JOIN on every 60-second
cycle was unnecessary and potentially disruptive. A single registration persists across
BLE disconnects. The correct pattern is one JOIN per HA restart (or per bridge process
start), not one per poll.

## Fix Options

| Option | Status |
|--------|--------|
| **Re-enable `DP_JOIN_DEVICE` once with PIN** | ✅ **Leading fix** — confirmed by app+remote coexistence test |
| Configurable "polling pause" window | Workaround only; JOIN fix makes this unnecessary |
| BLE notification mode — connect once, stay connected | Overkill; blocks remote scanning permanently |
| Increase poll interval significantly | Reduces exposure but doesn't fix the root cause |

## Related Files

- `aquaclean_console_app/bluetooth_le/LE/AriendiSecurity.py` — `aquacleanBridgeId`, KE Request
- `aquaclean_console_app/aquaclean_core/Clients/AlbaClient.py` — poll path (does NOT call `start_user_session`)
- `docs/developer/alba-ble-encryption.md` — Arendi protocol full analysis
- `docs/developer/ble-traffic-capture.md` — how to use PCA10059 with Wireshark
