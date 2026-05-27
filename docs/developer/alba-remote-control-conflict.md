# Alba — Remote Control vs. Bridge Conflict

## Symptom

When the HACS integration (or standalone bridge) is polling an AquaClean Alba:

- The physical Geberit remote control shows a **yellow exclamation mark** — its
  application-layer pairing with the toilet is invalidated.
- Re-pairing the remote **fails with a red exclamation mark** while the integration
  is running.
- Re-pairing **succeeds** only after disabling the integration.
- The Geberit Home App also cannot connect while the integration is running, even
  in the ~55-second gaps between polls.

This is **not** a timing issue. Yellow = application-layer deregistration. Red on
re-pair = active rejection by the toilet, not a timeout.

## Why This Is Not a Simple Timing Problem

With a 60-second poll interval, the bridge connects for ~5 seconds per cycle (7.7%
occupancy). The device should be free for ~55 seconds. Yet the app and remote cannot
connect even in that window. The toilet is actively rejecting them, not just busy.

## Root Cause Hypothesis — Arendi Session Ownership

> **Updated 2026-05-27:** Testing ruled out KE alone as the trigger — see
> "Narrowed Root Cause" under Investigation Steps. The current best hypothesis
> is that ownership is claimed on the first successful encrypted DpId response
> after KE, not by KE itself.

The Arendi security handshake (SABM → Version → EP → KE) is both encryption setup
and **client authentication**. The KE Request includes a CMAC authenticating the
client using `aquacleanBridgeId` (a fixed value shared by the bridge and the
official Geberit Home App).

The physical remote control almost certainly uses a **device-specific ID** registered
during physical pairing (NFC touch or dedicated pairing procedure).

**The toilet likely implements a "last registered owner" model:** whichever client
most recently completed a successful encrypted data exchange is the authorised session
owner. When the bridge reads DpIds every 60 seconds, it continuously re-claims
ownership. The remote's subsequent connection attempts present its device-specific ID,
which no longer matches the stored owner → the toilet rejects it → red exclamation.

Yellow exclamation = the remote's firmware has been kicked out enough times that it
flags "lost pairing" locally.

Re-pairing via NFC or the toilet's pairing mode re-registers the remote as owner —
but the bridge re-takes ownership within 60 seconds.

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
| `DP_JOIN_DEVICE` on every poll | ❌ Ruled out: removed in v3.0.0, conflict persists (MuusLee, 2026-05-27) |
| Arendi KE handshake alone (keyset 0) | ❌ Ruled out: wrong-PIN test — KE completes but remote not displaced when no DpId reads follow (MuusLee, 2026-05-27) |

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

**Open question (pending MuusLee confirmation):** did the Geberit App show the PIN
prompt immediately upon finding the device, or only after a ~15-second pause?
If immediate, the app tried `DP_JOIN_DEVICE` right after KE and failed before doing
any DpId reads — confirming the comparison below. If there was a long pause first,
the app may have done DpId reads before the JOIN attempt.

### Narrowed Root Cause — DpId Read Cycle After KE

Combining Steps 0 and 1:

| Scenario | KE | DpId reads | JOIN | Remote displaced? |
|---|---|---|---|---|
| v3.0.0 bridge (normal poll) | ✓ keyset 0 | ✓ full read cycle | ✗ removed | **Yes** |
| Wrong-PIN app (fresh phone) | ✓ keyset 0 | ✗ probably none | ✗ failed | **No** |

Both scenarios completed the KE handshake with keyset 0 and did not call a
successful JOIN. The only difference is the bridge performs a full DpId read cycle
after KE; the wrong-PIN app almost certainly does not — on first-time setup the
app presents the PIN prompt immediately after finding the device, meaning it
attempts JOIN right after KE and fails before reading any DpIds.

**Current best hypothesis:** the device registers the "active session owner" on the
first successful encrypted DpId response after KE. A session that completes KE but
then immediately fails at the application layer (wrong PIN) does not claim ownership.
A session that completes KE and then reads DpIds does.

This supersedes the earlier hypothesis that KE alone claims ownership.

**Implication for a fix:** the bridge cannot avoid the DpId reads (that is the
entire purpose of the poll). The only viable paths are:
- A "yield ownership" DpId write before disconnect (unknown if one exists)
- Per-keyset ownership tracking on the device (bridge keyset 0 and remote keyset 1
  coexist without conflict) — the PCA10059 sniff is needed to confirm this

### Step 2 — PCA10059 BLE Sniff (only if Steps 0 and 1 confirm KE is the culprit)

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

**Open question:**
Does the toilet track session ownership **per keyset** (keyset 0 and keyset 1 can
coexist, owned by different clients simultaneously) or **globally** (only one active
owner regardless of keyset)?

If per-keyset: bridge (keyset 0) and remote (keyset 1) should be able to coexist, and
the deregistration must have a different cause.
If global: bridge's keyset-0 KE every 60 s displaces the remote's keyset-1 registration
→ confirmed root cause. The PCA10059 sniff is the only way to distinguish these two
models without guessing.

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

## DP_JOIN_DEVICE — Paused Pending Remote Conflict Resolution

b23 added application-layer `DP_JOIN_DEVICE` (DpId 543) to `post_connect()`.
MuusLee confirmed JOIN completes without a PIN. However, calling JOIN on every
60-second poll cycle is a potential aggravator: if JOIN resets the device's
application-layer client registry, it could displace the remote's registration
at the application layer (distinct from the Arendi KE session ownership question).

The JOIN call has been **removed from `post_connect()`** for now. The `join()` method
in `Ble20Client.py` and the `alba_pin` HACS config field are retained — once the
remote conflict is understood, JOIN can be re-enabled with appropriate safeguards
(e.g., one-shot on first poll, not repeated on every cycle).

## Fix Options (pending sniff confirmation)

| Option | Trade-off |
|--------|-----------|
| Make bridge use the same client ID as the remote | Breaks app coexistence if toilet is single-owner per ID |
| Configurable "polling pause" window | User can trigger re-pairing during pause; manual workaround |
| BLE notification mode — connect once, stay connected | Bridge becomes permanent BLE owner; remote can never reconnect |
| Increase poll interval significantly (e.g. 5+ min) | Reduces kick frequency; remote degrades more slowly but still degrades |

The correct fix cannot be determined without the sniff confirming the session
ownership mechanism.

## Related Files

- `aquaclean_console_app/bluetooth_le/LE/AriendiSecurity.py` — `aquacleanBridgeId`, KE Request
- `aquaclean_console_app/aquaclean_core/Clients/AlbaClient.py` — poll path (does NOT call `start_user_session`)
- `docs/developer/alba-ble-encryption.md` — Arendi protocol full analysis
- `docs/developer/ble-traffic-capture.md` — how to use PCA10059 with Wireshark
