# Geberit Home App — Mera Comfort Onboarding Protocol

Analysis of the first-time connection from the Geberit Home App (iOS) to a
Geberit AquaClean Mera Comfort, captured with an nRF52840 sniffer.

Capture file: `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/on-board-geberit-Home-app-to-mera.pcapng`
Decoded analysis: `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/on-board-geberit-Home-app-to-mera.md`

Device: `38:AB:41:2A:0D:67` (Geberit AquaClean Mera Comfort)
BLE LL encryption: **none** (confirmed — same unencrypted path as bridge)

---

## BLE Advertising payload

The toilet advertises manufacturer-specific data only — no local name.

| AD type | Value | Notes |
|---------|-------|-------|
| `0xFF` | Company `0x0100`, data `00 31 34 36 32 31` | State byte + article chars |
| `0x02` | `0x3EA0` | 16-bit UUID (incomplete list) |

Company `0x0100` = TomTom International BV (Bluetooth SIG assigned).
Confirmed from `on-board-geberit-Home-app-to-mera.pcapng` (tshark: `company_id=0x0100`).

Manufacturer-specific payload layout:
- Byte 0: state byte (`0x00` = idle, `0xAA` = emergency connect permitted, `0x01` = button pressed)
- Bytes 1–5: article number ASCII prefix (e.g. `"14621"`)

The app identifies the toilet exclusively by company ID `0x0100` in manufacturer-specific data, not by local name or UUID alone.

---

## GATT service and handle map

| Handle | Type | Role |
|--------|------|------|
| `0x0003` | Write (ATT_WRITE_CMD) | Procedure requests (app → toilet) |
| `0x0006` | Write | Continuation write for multi-frame requests |
| `0x000F` | Notify | A5 — primary response channel (SINGLE + FIRST frames) |
| `0x0010` | CCCD | Enable notify on A5 |
| `0x0013` | Notify | A6 — CONS continuation frames |
| `0x0014` | CCCD | Enable notify on A6 |
| `0x0017` | Notify | A7 — CONS continuation frames |
| `0x0018` | CCCD | Enable notify on A7 |
| `0x001B` | Notify | A8 — CONS continuation frames |
| `0x001C` | CCCD | Enable notify on A8 |
| `0x0020` | Read | Button-press confirmation — returns `b"ro"` while waiting |
| `0x002C` | CCCD | Non-data service (OTA?) — app enables notify at startup |

---

## Full onboarding sequence

### Phase 1 — GATT setup (t ≈ 24–26 s)

1. MTU exchange
2. READ BY GROUP TYPE — service discovery
3. READ BY TYPE — characteristic discovery (handles `0x0002`–`0x001a`, `0x002a`, `0x002b`)
4. FIND INFO — descriptor discovery (CCCDs + user descriptions)
5. WRITE REQ `0x002C` = enable notify (non-data service)
6. WRITE REQ `0x0010`, `0x0014`, `0x0018`, `0x001C` = enable notify on A5/A6/A7/A8

### Phase 2 — Identification (t ≈ 26–28 s)

| Proc | Name | Notes |
|------|------|-------|
| `0x82` | GetDeviceIdentification | SAP number, serial, variant |
| `0x05` | GetNodeInventory | Returns list of subsystem node IDs: `[3,4,5,6,7,8,9,0xa,0xb,0xc,0xe,0xf]` |
| `0x81` | GetSOCApplicationVersions | Firmware version strings |
| `0x0E` | GetSystemParameterList (batch 1) | Indices `[4,5,6,7,8,9,10,11,12,14]` |
| `0x0E` | GetSystemParameterList (batch 2) | Indices `[1,15]` |
| `0x11` ×4 | SubscribeNodeFirmwareVersion | Node groups: `[1,3,4,5]` `[6,7,8,9]` `[a,b,c,e]` `[f]` |
| `0x13` ×4 | SubscribeNodeStoredSettings | Node groups: `[1,3,4,5]` `[6,7,8,9]` `[a,b,c,e]` `[f,d]` |

Note: proc `0x0E` may be the same as `0x0D` (GetSystemParameterList) — needs verification
against raw frame bytes. The app queries SPL indices 8, 9, 10 on the Mera Comfort without
issue; our bridge deliberately excludes them (conservative safety margin).

### Phase 3 — Button press (t ≈ 28–39 s, ~10 s gap)

The app reads handle `0x0020` → receives `b"ro"` → waits for the user to press the button
on the physical toilet. After button press, the toilet signals confirmation (likely via an
unsolicited notify on `0x000F`) and the app proceeds.

No PIN entry, no BLE SMP pairing — the physical button press IS the authentication.

### Phase 4 — Settings read (t ≈ 39–41 s)

| Proc | Args | Name |
|------|------|------|
| `0x07` ×11 | nodes `0–9`, `0xd` | GetPerNodeProfileSetting |
| `0x0A` ×10 | IDs `0–9` | GetActiveCommonSetting |

---

## Mock implementation requirements

For a `mock-geberit-mera.py` that satisfies the Geberit Home App:

**Must implement:**
1. Advertising: manufacturer-specific, company `0x0001`, state byte `0x00` + 5-char article
2. GATT: handles `0x0003`, `0x0006`, `0x000F`–`0x001C` (with CCCDs), `0x0020`, `0x002C`
3. Proc `0x82` → fake SAP number + serial
4. Proc `0x05` → node list `[3,4,5,6,7,8,9,0xa,0xb,0xc,0xe,0xf]`
5. Proc `0x81` → fake version strings
6. Proc `0x0E` / `0x0D` → SPL values for queried indices (zeros are fine)
7. Proc `0x11` + `0x13` → stub empty responses per node group
8. Handle `0x0020` read → `b"ro"` initially; web UI "Press Button" triggers notify on `0x000F` + flips state
9. Proc `0x07` → stub empty per-node profile response (11 nodes)
10. Proc `0x0A` → stub common setting values for IDs 0–9

**Not needed:** proc `0x55` (GetDeviceRegistrationLevel), proc `0x44` (PIN), proc `0x86` (InitialOperationDate).

---

## Unknown / new procedures

| Proc | Observed behaviour | Status |
|------|-------------------|--------|
| `0x05` | Returns 12 node IDs | New — not in bridge or known proc list |
| `0x0E` | SPL batch query (may be `0x0D`) | Needs raw frame verification |
| `0x11` | Node firmware version subscribe | New |
| `0x13` | Node stored settings subscribe | New |
