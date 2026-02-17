# BLE Coexistence — Python Bridge and Geberit Home App

## The AquaClean is a single-central BLE device

The AquaClean Mera acts as a **BLE GATT server** (peripheral).  It accepts connections from BLE centrals — your Python bridge, the Geberit Home app on your phone, or the physical remote control.

The device is designed for **one active central at a time**.  While a central is connected:

- The **physical remote control is disabled** (documented in the Geberit manual).
- A second central attempting to connect will either be rejected, or — depending on firmware — may cause the device to drop the existing connection to make room.

This is standard BLE peripheral behaviour, not a limitation of this software.

---

## Conflict with the Geberit Home app

If the Python bridge is running in **persistent** BLE mode (permanent connection), the official **Geberit Home** app on your phone will fail to connect as long as the bridge is connected.  Conversely, if the Geberit Home app is open and connected, the bridge cannot establish a BLE session.

**On-demand mode resolves this in practice.**  Because the bridge only holds the BLE connection for ~1–2 seconds per request and then releases it, the Geberit Home app can connect freely between polls.  The two can coexist as long as they do not try to connect at exactly the same moment.

---

## Stale connections after a crash

If the Python process is killed without cleanly disconnecting, the AquaClean may continue to consider itself connected to the previous central until the BLE **supervision timeout** expires (typically 5–10 seconds).  During this window a new connection attempt may be refused.

If the bridge cannot reconnect after a crash or restart, wait a few seconds and retry.  If the device is still unresponsive, power-cycling the AquaClean clears any stale link state.

---

## Practical rules

| Situation | Result |
|-----------|--------|
| Bridge in persistent mode + Geberit Home app | App cannot connect while bridge is connected |
| Bridge in on-demand mode + Geberit Home app | Coexist — connect windows are short; occasional timing conflict possible |
| Bridge crashed without clean disconnect | Wait for supervision timeout (~10 s), then retry |
| Device unresponsive after days of persistent use | Power-cycle to reset; switch to on-demand mode |
