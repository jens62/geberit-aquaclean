# BLE Coexistence — Python Bridge and Geberit Home App

## The AquaClean is a single-central BLE device

The AquaClean Mera acts as a **BLE GATT server** (peripheral).  It accepts connections from BLE centrals — your Python bridge, the Geberit Home app on your phone, or the physical remote control.

The device is designed for **one active central at a time**.  While a central is connected:

- The **physical remote control is disabled** (documented in the Geberit manual).
- A second central attempting to connect will either be rejected, or — depending on firmware — may cause the device to drop the existing connection to make room.

This is standard BLE peripheral behaviour, not a limitation of this software.

### Why "one central at a time" — BLE spec vs. implementation

This is accurate in practice but the underlying reason is **implementation-specific**, not a hard BLE specification limit — and BLE 5 does not change it.

**What the spec actually says:**
The BLE specification (4.x and 5.x) has always allowed a peripheral to hold multiple simultaneous connections to different centrals.  The spec defines `Max_Connections` as an implementation parameter, not a fixed value of 1.

**Why "typically one" is still true:**
Constrained devices (microcontrollers running NimBLE, SoftDevice, etc.) almost universally cap at one connection in their firmware — limited RAM for connection contexts, scheduling complexity, and power budget.  This is a chip/stack decision, not a BLE version constraint.

**What BLE 5 actually added:**
Extended advertising, 2 Mbps PHY, coded PHY (long range), and advertising sets.  None of these change the peripheral multi-central connection model.  BLE 5 did not introduce "multi-central peripheral" as a feature.

**For the Geberit specifically:**
The Alba device stops advertising while a central is connected (confirmed in testing), so only one central can be BLE-connected at any moment.  "App + remote coexist" does not mean they are simultaneously BLE-connected — it means both hold valid application-layer registry entries (`DP_JOIN_DEVICE`) and can each connect and use the device without displacing the other.  Their BLE connections are sequential, not simultaneous.

---

## Conflict with the Geberit Home app

If the Python bridge is running in **persistent** BLE mode (permanent connection), the official **Geberit Home** app on your phone will fail to connect as long as the bridge is connected.  Conversely, if the Geberit Home app is open and connected, the bridge cannot establish a BLE session.

**On-demand mode resolves this in practice.**  Because the bridge only holds the BLE connection for ~1–2 seconds per request and then releases it, the Geberit Home app can connect freely between polls.  The two can coexist as long as they do not try to connect at exactly the same moment.

---

## Conflict with the physical remote control

The physical Geberit remote control connects to the toilet as a BLE central — exactly like the bridge and the Geberit Home app.  While any central holds a BLE connection, all other centrals are locked out.

The Geberit manuals state this explicitly:

> "The remote control function of the Geberit AquaClean shower toilet is deactivated while the shower toilet is connected to the Geberit Home App."
> — GEBERIT AQUACLEAN ALBA USER MANUAL, chapter 4 "Operating concept"

The equivalent statement appears in the Geberit AquaClean Mera Comfort user manual as well.  The same restriction applies to the bridge: **while the bridge holds a BLE connection, the remote does not work.**

**Mitigation — use on-demand mode with a reasonable poll interval.**
In on-demand mode the bridge holds the BLE link for only ~1–2 seconds per poll and releases it immediately afterwards.  With a 30-second poll interval the remote has approximately 28–29 seconds free per cycle.  It may have to wait for the current poll to finish before it can connect, but it will not be permanently locked out.

**Persistent mode is incompatible with remote control use.**
In persistent mode the bridge never releases the BLE connection, so the remote stays locked out for as long as the bridge is running.

---

## Stale connections after a crash

If the Python process is killed without cleanly disconnecting, the AquaClean may continue to consider itself connected to the previous central until the BLE **supervision timeout** expires (typically 5–10 seconds).  During this window a new connection attempt may be refused.

If the bridge cannot reconnect after a crash or restart, wait a few seconds and retry.  If the device is still unresponsive, power-cycling the AquaClean clears any stale link state.

---

## Practical rules

| Situation | Result |
|-----------|--------|
| Bridge in persistent mode + Geberit Home app | App cannot connect while bridge is connected |
| Bridge in persistent mode + physical remote | Remote locked out indefinitely — switch to on-demand mode |
| Bridge in on-demand mode + Geberit Home app | Coexist — connect windows are short; occasional timing conflict possible |
| Bridge in on-demand mode + physical remote | Remote free for ~28–29 s per 30 s poll cycle; may wait briefly for current poll to finish |
| Bridge crashed without clean disconnect | Wait for supervision timeout (~10 s), then retry |
| Device unresponsive after days of persistent use | Power-cycle to reset; switch to on-demand mode |
