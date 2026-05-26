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

The Arendi security handshake (SABM → Version → EP → KE) is both encryption setup
and **client authentication**. The KE Request includes a CMAC authenticating the
client using `aquacleanBridgeId` (a fixed value shared by the bridge and the
official Geberit Home App).

The physical remote control almost certainly uses a **device-specific ID** registered
during physical pairing (NFC touch or dedicated pairing procedure).

**The toilet likely implements a "last registered owner" model:** whichever client
most recently completed a successful KE exchange is the authorised session owner.
When the bridge does SABM + KE every 60 seconds, it continuously re-claims ownership.
The remote's subsequent connection attempts present its device-specific ID, which no
longer matches the stored owner → the toilet rejects it → red exclamation.

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
