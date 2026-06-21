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
2. READ BY TYPE UUID `0x2803` (Characteristic Declaration) — 17× across the full handle
   range, walking handle-by-handle to discover all characteristics in the Geberit service.
   Confirmed from nRF52840 capture at t=24.6s–25.6s.
   Note: the nRF sniffer lags ~200 ms after CONNECT_IND; the first few frames (including any
   Read By Type UUID `0x3A2B` probe and Read By Group Type service discovery) may precede
   the first captured packet.
3. FIND INFO — descriptor discovery (CCCDs + user descriptions)
4. WRITE REQ `0x002C` = enable notify (non-data service)
5. WRITE REQ `0x0010`, `0x0014`, `0x0018`, `0x001C` = enable notify on A5/A6/A7/A8

**A6 spontaneous notify burst** — immediately after CCCD-A6 is enabled (t=25.9s), the toilet
sends **11 unsolicited notifies on A6** (handle `0x0013`) before CCCD-A7 is even enabled.
All carry the identical 20-byte payload:

```
80 01 30 14 0c 03 00 03 00 00 00 00 31 30 00 12 00 b7 08 00
```

Frame header `0x80` = INFO frame, no HasMsgType/IsSubFrameCount bits set (differs from the
mock's `0x91`). This appears to be a device-state broadcast the toilet sends on every new
CCCD-enable, not a button-press signal.

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

The app performs a **Read By Type UUID `0x2A00`** (Device Name) at t=29.7s and receives
`b"ro"` — the button-ready indicator. The mock sets the adapter alias to `"ro"` for
exactly this check. The App then waits for the user to press the physical button on the
toilet.

After button press the toilet signals confirmation. The actual notify is **not present in
the nRF52840 capture** (likely packet loss during the ~9-second button-hold window); the
expected channel and payload remain unconfirmed from captures. The App proceeds to Phase 4
(proc `0x07`) without any further ATT traffic visible in the trace.

No PIN entry, no BLE SMP pairing — the physical button press IS the authentication.

### Phase 4 — Settings read (t ≈ 39–41 s)

| Proc | Args | Name |
|------|------|------|
| `0x07` ×11 | nodes `0–9`, `0xd` | GetPerNodeProfileSetting |
| `0x0A` ×10 | IDs `0–9` | GetActiveCommonSetting |

---

## Confirmed onboarding sequence — two-connection pattern (v2.14.1, 2026-06-21)

Source: `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard-1.pcapng`  
Mapping: `...nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard-mapping-1.md`  
Device firmware: **RS28.0 TS199** (SOC 10.18)

The Geberit Home App uses **two sequential BLE connections** for first-time onboarding:

### Connection 1 — button detection (~15 s)

1. GATT characteristic discovery (18× Read By Type UUID 0x2803)
2. Enable notify on CCCD-A5, A6, A7 → device sends InfoFrame burst on A6 immediately
3. Enable notify on CCCD-A8
4. GetDeviceIdentification (proc 0x82), GetNodeList (proc 0x05), GetSOCApplicationVersions (proc 0x81)
5. GetFirmwareVersionList (proc 0x0E): 12 component IDs [1,3,4,5,6,7,8,9,10,11,12,14] → **RS28.0 TS199**; then component [15]
6. SubscribeNotif_0x13 × 4 (init handshakes)
7. **Device Name read (UUID 0x2A00) → `"ro"`** — confirms button is held
8. Proc 0x07 × 10 (node IDs 04,02,05,03,09,01,00,0d,08,07) — GetPerNodeProfileSetting for all nodes; device sends InfoFrames on A5
9. GetStoredProfileSetting (proc 0x53) × 10 (read-only): AnalShowerPressure=2, OscillatorState=3, LadyShowerPressure=2, AnalShowerPosition=2, WaterTemperature=1, WcSeatHeat=1, LadyShowerPosition=0, DryerTemperature=0, OdourExtraction=1, DryerState=0
10. **App disconnects** — "Release the button" screen appears, user releases button

### Connection 2 — onboarding (≥19 s, then continuous polling)

Steps 1–6 (GATT discovery through SubscribeNotif) repeat identically. Then:

7. **GetStoredProfileSetting × 10** (same settings, read again)
8. **SetStoredProfileSetting × 3** (init writes): AnalShowerPressure=2, OscillatorState=3, LadyShowerPressure=2
9. **GetStoredCommonSetting × 10**: Color(2)=Magenta, Brightness(1)=3, Mode(3)=WhenApproached, id4=2, id6=1, id7=1, id5=0, id8=0, WaterHardness(0)=1, id9=0
10. **GetSystemParameterList**: params [0,1,2,3,4,5,6,7,8,9,10,11] — all 12 including indices 8/9/10
11. **GetFilterStatus (proc 0x59)**:
    - First query: IDs [0–7] → empty response (probe)
    - Second query: IDs [0–11] → days=348, resets=5, last_reset_date (real data)
12. **UnknownProc_0x55** (GetDeviceRegistrationLevel) — called **once per session** at init completion
13. **GetDeviceInitialOperationDate (proc 0x86)** — called once
14. **Continuous GetSystemParameterList polling** at ~0.45 s interval

**App shows "Connection established" + Save button** after step 7–8 complete (SubscribeNotif_0x13 ×4 ACK).  
**User clicks Save** → polling continues; capture ends at step 14.

### Why two connections?

Connection 1 is the **button detection cycle**: the app verifies the button is held (Device Name = `"ro"`), reads all current settings, and confirms via proc 0x07. Only after the user releases the button does it reconnect for the actual onboarding commit.

Connection 2 is the **onboarding cycle**: same init, then writes profile settings back, reads all device state, and begins normal operation polling.

---

## iOS GATT cache — clearing mechanism (mock v1.21.0)

iOS caches GATT handle maps per peripheral Bluetooth address across reboots for non-bonded
peripherals. When the mock's handle layout changes between sessions (e.g. different version
of `bluez_peripheral` or different characteristic ordering), iOS uses stale handles and
receives ATT Invalid Handle errors → immediate disconnect → "connection failed" in App.

**Fix: BlueZ GATT re-registration.** Calling `service.unregister()` followed by
`service.register()` on the `ServiceCollection` causes BlueZ to detect a GATT database
change and send a **Service Changed indication** on its built-in UUID 0x2A05 at handle
0x0003 (CCCD at 0x0004). iOS has auto-subscribed to handle 0x0004, receives the indication,
and discards its cached handle map.

**One-shot flag:** re-registration fires only on the *first* connection per mock session
(600ms after BLE connect). Once fired, `_service_changed_fired = True` prevents
re-registration on subsequent connections, so iOS can complete uninterrupted GATT discovery.

**Two-connection pattern:**
1. Connection 1 — re-registration fires at 600ms → iOS receives Service Changed → disconnects
   (App shows "connection failed") — **correct and expected**
2. Connection 2 — re-registration skipped → iOS does fresh Read By Type 0x2803 handle-walking
   → discovers Geberit chars → enables A5–A8 CCCDs → proceeds to Phase 2

**Pitfall: duplicate 0x2A05 in Geberit service (v1.18.0–v1.20.0).** Adding a custom
`0x2A05` INDICATE characteristic inside the Geberit service created a second 0x2A05 at
handle 0x0016. iOS probed handle 0x0016 mid-discovery, then Service Changed arrived from
re-registration at 600ms and iOS aborted. Removed in v1.21.0 — only BlueZ's built-in
0x2A05 (0x0001 service, handle 0x0003) is needed.

---

## iOS vs Android discovery (2026-06-20)

**iOS (Geberit Home App):** mock is NOT found, even with correct company `0x0100` + UUID `0x3EA0`.
**Android (Geberit Home App):** mock IS found with company `0x0100` ✓ (confirmed 2026-06-20).

Root cause for iOS failure: the ASUS USB-BT500 (RTL8761B, BT5.0) uses the extended HCI path
(`LE_Set_Extended_Advertising_Enable`, opcode 0x2039) with the LEGACY-PDU flag set. Whether
the RTL8761B actually transmits ADV_IND (legacy) or ADV_EXT_IND (extended) on air is unconfirmed.
nRF Connect (iOS) sees the mock — but nRF Connect uses extended scanning which sees both PDU types.
The Geberit Home App on iOS likely uses passive legacy scan for the Mera path (no service UUID
filter = scan-all), which may not see ADV_EXT_IND. Definitive test: nRF52840 sniffer on advertising
channels while mock runs, to observe the actual on-air PDU type. Workaround: use a BT4.0 adapter
(forces legacy HCI path, guaranteed ADV_IND on air).

---

## Android GATT connection trace (2026-06-20, mock v1.14.0)

Capture: `mock-geberit-mera_btmon_2026-06-20_08-23.btsnoop`

Android App connected (MAC `78:42:1C:38:DE:16`), showed "press button" screen, then
"connection failed" after 31 seconds. ATT sequence:

| Time | ATT PDU | Result |
|------|---------|--------|
| 08:25:12.513 | Exchange MTU (client_mtu=517) | ✓ |
| 08:25:12.568 | Read By Type uuid=0x3A2B, range 0x0001–0xFFFF | Error 0x0A (Not Found) |
| 08:25:12.644 | Read By Group Type uuid=0x0028 (service discovery) | See below |
| 08:25:12.644 | Write Req att_handle=0x0097 value=0100 (CCCD enable, cached) | Error 0x01 (Invalid Handle) |
| 08:25:12.744 | Read By Group Type Resp | Only 0x1801 (0x0001–0x0005) and 0x1800 (0x0014–0xFFFF) |
| 08:25:12.771 | Write Req att_handle=0x0004 value=0200 | Service Changed subscription |
| 08:25:14 | Button InfoFrame notify received by App | App did nothing further |
| 08:25:43 | Disconnect (Remote User Terminated) | "connection failed" on App |

**Three simultaneous failures at 08:25:12:**

1. **UUID 0x3A2B not found** — the App probes for a 16-bit UUID characteristic before service
   discovery. This UUID is present on the real Mera but not in the mock. Unknown what it is;
   likely relates to the button-state READ characteristic (handle 0x0020 on real device).

2. **Handle 0x0097 → Invalid Handle** — Android cached GATT handles from a prior connection
   to the mock (when the mock had its A5 CCCD at handle 0x0097). On restart, BlueZ reassigns
   handles and 0x0097 is no longer valid. Fix: clear Android Bluetooth cache before each test
   (Settings → Apps → Bluetooth → Clear Cache) to force re-discovery.

3. **Service discovery: custom Geberit service not visible** — BlueZ returns 0x1801 and 0x1800
   (both 16-bit UUID services) in one Read By Group Type response, skipping the custom Geberit
   service (128-bit UUID, handles 0x0006–0x0013). Android stops discovery when end_group=0xFFFF
   is seen. This is a BlueZ GATT server limitation affecting Android but not iOS/bleak (which
   discover by characteristic UUID directly). The App can't proceed because it can't find the
   Geberit write or notify characteristic handles.

---

## Mock implementation requirements

For a `mock-geberit-mera.py` that satisfies the Geberit Home App:

**Must implement:**
1. Advertising: manufacturer-specific, company `0x0100`, state byte `0x00` + 5-char article
   (+ optional state_B byte + 2-char RS fw prefix for the 9-byte "11-byte variant" format)
2. GATT: write chars at 0x0003/0x0006, notify A5–A8 with CCCDs, READ char at 0x0020,
   non-data CCCD at 0x002C — all visible to Android's Read By Group Type discovery
3. GATT service must be discoverable via Android `Read By Group Type` — requires investigation
   of BlueZ D-Bus GATT handle allocation (current mock invisible to Android service discovery)
4. READ characteristic (equiv. handle 0x0020) → returns `b"ro"` until button pressed;
   UUID unknown — may be `3334429d-90f3-4c41-a02d-5cb3a43e0000` (inferred from UUID pattern)
5. UUID 0x3A2B probed by App — characteristic unknown; probably related to (4)
6. Proc `0x82` → fake SAP number + serial ✓ (implemented)
7. Proc `0x05` → node list `[3,4,5,6,7,8,9,0xa,0xb,0xc,0xe,0xf]`
8. Proc `0x81` → fake version strings ✓ (implemented)
9. Proc `0x0E` / `0x0D` → SPL values for queried indices (zeros are fine) ✓ (implemented)
10. Proc `0x11` + `0x13` → stub empty responses per node group
11. Handle `0x0020` read → `b"ro"` initially; web UI "Press Button" triggers notify on A5 + flips state
12. Proc `0x07` → stub empty per-node profile response (11 nodes)
13. Proc `0x0A` → stub common setting values for IDs 0–9

**Also needed (confirmed from two-connection capture):**
14. Proc `0x55` (GetDeviceRegistrationLevel) — called once per session at init completion (Connection 2 only)
15. Proc `0x86` (GetDeviceInitialOperationDate) — called once in Connection 2
16. Proc `0x59` (GetFilterStatus) — called twice: first IDs [0–7] (probe, returns empty), then IDs [0–11] (full response)
17. SetStoredProfileSetting (proc `0x54`) — mock must ACK writes of AnalShowerPressure, OscillatorState, LadyShowerPressure in Connection 2

**Not needed:** proc `0x44` (PIN — physical button replaces app-layer auth).

---

## Unknown / new procedures

| Proc | Observed behaviour | Status |
|------|-------------------|--------|
| `0x05` | Returns 12 node IDs | New — not in bridge or known proc list |
| `0x0E` | GetFirmwareVersionList — arg = list of component IDs; called twice: [1,3,4,5,6,7,8,9,10,11,12,14] then [15]; returns RS/TS version strings. **Distinct from `0x0D`.** | Confirmed 2026-06-21 |
| `0x11` | Node firmware version subscribe | New |
| `0x13` | Node stored settings subscribe | New |

## Unknown GATT characteristics

| UUID / handle | Observed | Status |
|---------------|----------|--------|
| UUID 0x3A2B (16-bit) | Probe fires ~95ms after connect, before app code runs; Not Found on mock | **CoreBluetooth OS-level probe** — not in app source (confirmed: 0 matches across all app files). iOS fires this internally for GATT cache fingerprinting. Not Found is correct; app never sees the result. Mock does not need to implement it. |
| Handle 0x0020 (READ) / UUID 0x2A00 | App reads Device Name via Read By Type 0x2A00 at t=29.7s; expects `b"ro"` | Mock sets adapter alias to `"ro"` ✓ — this is the standard Device Name char, not a custom Geberit UUID |
| Handle 0x002C (CCCD) | App enables notify; non-data service (OTA?) | Missing from mock |

## Write characteristics — real device has 4 (mock has 2)

The app's connection code discovers characteristics **a1, a2, a3, a4** (all WRITE_WITHOUT_RESPONSE).
The mock only registers a1 (`5cb3a13e`) and a2 (`5cb3a23e`).
The nRF52840 capture shows only handles 0x0003 and 0x0006 actively used during onboarding,
so a3/a4 appear to be optional or used post-onboarding. Worth adding to the mock if the app
fails after the identification phase.
