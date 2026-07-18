# Geberit Home App — Mera Comfort Onboarding Protocol

Analysis of the first-time connection from the Geberit Home App (iOS) to a
Geberit AquaClean Mera Comfort, captured with an nRF52840 sniffer.

Capture file: `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/on-board-geberit-Home-app-to-mera.pcapng`
Decoded analysis: `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/on-board-geberit-Home-app-to-mera.md`

Device: `38:AB:41:2A:0D:67` (Geberit AquaClean Mera Comfort)
BLE LL encryption: **none** (confirmed — same unencrypted path as bridge)

---

## BLE Advertising payload

**Corrected 2026-07-18** — the "one 11-byte payload" model below was wrong. The real device
splits its manufacturer-specific data across **two separate packets**, not one AD structure:
ADV_IND carries a 6-byte payload (state + article) under company `0x0100`; a *second*,
independent Manufacturer Specific Data AD entry — using a throwaway/non-standard "company ID"
that's really just 2 of its own payload bytes — lives in the **SCAN_RSP** packet and carries
the RS-firmware-prefix tail. A scanner that merges ADV_IND+SCAN_RSP into one device view (nRF
Connect, and apparently the app itself) sees what looks like two Manufacturer Data blocks for
the same device. Confirmed byte-for-byte via `tools/nrf-ble-analyze.py --adv` (extended the
same day to show every AD entry, not just the first) against
`local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/onboarding-real-mera.pcapng`,
and independently via a live nRF Connect (Android) scan against the real device.

The toilet advertises no local name in ADV_IND — the name (`"Geberit AC PRO"`) is in SCAN_RSP.

**ADV_IND** — one AD entry, `0xFF` Company `0x0100`, 6-byte payload (`length=9`: type+company+data):

| Offset (in payload, after company ID) | Value | Name | Notes |
|--------|-------|------|-------|
| 0 | `0x00` / `0xAA` (adv byte after company) | state_A | `IsEmergencyConnectPermitted` — confirmed 2026-07-18: on button press the low byte of the COMPANY ID itself flips to `0xAA` (i.e. the ADV_IND's company field becomes `0x01AA`), not a separate payload byte |
| 1 | `0x00` / `0x01` | state_B | **`IsButtonPressed = (byte == 0x01)`** ← iOS/Android both key onboarding-selection off this |
| 2–6 | e.g. `"14621"` | article | 5-char ASCII article number |

Live nRF Connect confirms both flags flip together on a real button press: idle
`company=0x0100, data=00 31 34 36 32 31`; after pressing the pairing button:
`company=0x01AA, data=01 31 34 36 32 31` (state_B also flips to `0x01`).

**SCAN_RSP** — separate AD entries: `0x09` Complete Local Name (`"Geberit AC PRO"`),
`0x12` Peripheral Connection Interval Range, `0x0A` Tx Power Level, and a second `0xFF`
Manufacturer-Specific-Data entry, 3 raw bytes `[0x00, rs_char1, rs_char2]` (dissected by
tshark as a bogus 2-byte "company ID" + 1 data byte, since it's just 2 ASCII digits
misaligned into the company-ID field position): the RS firmware major-version prefix, e.g.
`00 32 38` = "28" (RS28.0, pre-update) in the older real capture, `00 33 30` = "30" (RS30.0,
post-update) in a 2026-07-18 live scan — consistent with the confirmed RS28→RS30 update.

Company `0x0100` = TomTom International BV (Bluetooth SIG assigned, reused by Geberit as a
convenient existing ID, unrelated to TomTom).

The app identifies the toilet by company ID `0x0100` in ADV_IND and reads the state_B byte
from every received advertisement to determine `IsButtonPressed`. Only when
`IsButtonPressed=True` does the app select the device and attempt a BLE connection. Whether
the app's onboarding-selection logic ever reads the SCAN_RSP's RS-firmware-prefix tail at all
is unconfirmed — the mock has worked for onboarding for months while sending that tail merged
into ADV_IND instead of split into SCAN_RSP, suggesting the app's selection logic doesn't
depend on that byte's exact packet placement.

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

**CCCD sequence and InfoFrame burst — detail from nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard-2.md:**

| Capture time | Event | Notes |
|---|---|---|
| t=68.8s | CCCD-A5 write (enable notify) | First CCCD |
| t=69.0s | CCCD-A6 write (enable notify) | |
| t=69.1s | CCCD-A7 write (enable notify) | |
| t=69.1s | **6× InfoFrame burst on A6** | Fires immediately after CCCD-A7; same 20-byte payload as Connection 1 |
| t=69.2s | CCCD-A8 write (enable notify) | Fourth CCCD |
| t=69.2s | **3× InfoFrame burst on A6** | Fires after CCCD-A8; same payload |

Total InfoFrames on A6: **9**. The burst fires on A6 (not A5). This sets `ConnectionState = Ready`
inside the app, which is required for `Connect()` to succeed (see "Fehler" investigation below).

The nRF52840 sniffer `.md` captures **all** spontaneous device-initiated notifies, labelled as
"Orphan Notify" (no preceding WRITE on the same characteristic). Confirmed at lines 60–65, 83–85,
114, 141–142, 180–181, 205. The absence of a frame type from the file means it was NOT sent.

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

## Button hold mechanism — confirmed from app source (2026-06-21)

Source: app source analysis (v2.14.1) + nRF52840 capture `nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard-1`.

### What "hold the button" actually does

Physically holding the toilet button causes the firmware to set `state_B = 0x01` in the BLE
advertisement manufacturer data (byte[2]). The iOS app's 15-second scan loop calls
`UpdateAdvertisingData()` on every received advertisement:

```csharp
IsButtonPressed             = (data[2] == 1)    // byte[2] of manufacturer payload
IsEmergencyConnectPermitted = (data[0] == 0xAA)
```

`AqCPressButtonProgressViewModel` scans for exactly 15 seconds (`m__E000 = 15000 ms`).
Within the scan loop, it skips every device where `IsButtonPressed = False`. When a device
with `IsButtonPressed = True` appears, it cancels the scan and initiates Connection 1.

Releasing the button reverts `state_B = 0x00` in the advertisement, but this does not
affect the connection that is already in progress.

### What happens during Connection 1 (button held)

The toilet does **not** continuously send anything special during button hold. All
device-initiated notifications (InfoFrame burst on A6) are sent on every connection
regardless of button state. There is no special channel. The button-held state is verified
in three one-shot checks:

| Time in capture | Check | Mechanism |
|----------------|-------|-----------|
| Pre-connection (scan) | `IsButtonPressed` | Advertisement `byte[2] == 0x01` |
| t=72.8s | Button still held | GATT Device Name (UUID 0x2A00) reads `"ro"` |
| t=79.6s–81.1s | Button detection ceremony | Proc 0x07 × 10 nodes [04,02,05,03,09,01,00,0d,08,07] |

After proc 0x07 responses, the app shows "Release the button". Connection 1 ends; Connection 2
starts for the actual onboarding commit.

### SecurityManager — not button-related

`AquaCleanProduct.Initialize()` checks `SecurityManager.Unlocked` before any GATT operations.
This is a **factory-creation license gate** (`BleProductManagerFactory.Create(unlock, ...)` is
called at app startup), not a button-state gate. It is always true for a legitimately running app.

### Mock implementation of button hold (v1.25.0)

1. **Advertisement byte[2]** starts at `0x00` (IsButtonPressed=False).
2. User clicks **"Press Button"** in the web UI **after** tapping "Connect" in the iOS app
   (within the 15-second scan window).
3. Mock calls `_update_advert(1)` → unregisters current advertisement via
   `LEAdvertisingManager1.UnregisterAdvertisement` → re-registers with `state_B=0x01`.
4. iOS scan receives the updated advertisement → `IsButtonPressed=True` → Connection 1 starts
   automatically — no further user action needed.
5. On BLE disconnect: `_update_advert(0)` reverts `state_B=0x00` for the next cycle.

---

## iOS GATT cache — clearing mechanism (mock v1.21.0, superseded by v1.25.x)

iOS caches GATT handle maps per peripheral Bluetooth address across reboots for non-bonded
peripherals. When the mock's handle layout changes between sessions (e.g. different version
of `bluez_peripheral` or different characteristic ordering), iOS uses stale handles and
receives ATT Invalid Handle errors → immediate disconnect → "connection failed" in App.

**v1.21.0 approach: deliberate BlueZ GATT re-registration.** Calling `service.unregister()`
followed by `service.register()` on the `ServiceCollection` causes BlueZ to detect a GATT
database change and send a **Service Changed indication** on its built-in UUID 0x2A05 at
handle 0x0003 (CCCD at 0x0004). iOS discards its cached handle map.

**ATT deadlock in v1.21.0 (why it was removed):** The re-registration at 600ms caused a
deadlock — iOS had pending ATT Read By Type responses in flight; BlueZ was waiting for iOS's
ATT_CONFIRMATION before delivering those responses; iOS was waiting for responses before
sending ATT_CONFIRMATION. Removed in v1.25.x in favour of the SC flush approach (see below).

**Pitfall: duplicate 0x2A05 in Geberit service (v1.18.0–v1.20.0).** Adding a custom
`0x2A05` INDICATE characteristic inside the Geberit service created a second 0x2A05 at
handle 0x0016. iOS probed handle 0x0016 mid-discovery, then Service Changed arrived from
re-registration at 600ms and iOS aborted. Removed in v1.21.0 — only BlueZ's built-in
0x2A05 (0x0001 service, handle 0x0003) is needed.

---

## iOS GATT cache — SC flush mechanism (v1.25.x, 2026-06-21)

**Root cause confirmed from btsnoop** (`mock-geberit-mera_btmon_2026-06-21_15-57.btsnoop`):

BlueZ automatically sends Service Changed (UUID `0x2A05`, handle `0x000A`, value `0100ffff`)
whenever the GattApplication was freshly registered since the last connection. This is
fundamental BlueZ behavior and cannot be suppressed.

**SC fires at ~485–497ms after connection**, triggered when iOS enables the SC CCCD
(handle `0x000B`) during the MTU exchange phase — before any Read By Type discovery.

iOS receives SC and simultaneously:
1. Starts fresh GATT discovery (`Read By Group Type`)
2. Reads stale cached handles from the previous session

Handle `0x001B` (A5 CCCD in the current GATT layout) is in the stale cache. BlueZ returns
**ATT Error 0x05 (Insufficient Authentication)** — it blocks all ATT reads while the SC
indication is pending acknowledgment from iOS. iOS enters 22 seconds of chaotic parallel
ATT requests and then gives up. **Connection 2 never happens.**

### SC flush — force-disconnect at 700ms

Exploit the two-connection pattern: SC fires in Connection 1 and clears the iOS cache.
Force-disconnect Connection 1 immediately after SC is received; iOS retries as Connection 2
with a clean cache and no pending SC.

1. **Connection 1** — SC fires automatically at ~497ms. Mock detects the connection and
   schedules `_sc_flush` in 700ms (200ms after SC, enough for iOS to receive it).
2. `_sc_flush` calls `org.bluez.Device1.Disconnect()`. iOS receives the disconnect and
   immediately retries (no "connection failed" shown — it's part of the onboarding flow).
3. **Connection 2** — BlueZ's "changed" flag is cleared (SC was already sent). iOS has no
   stale cache (SC caused iOS to discard it). Clean 18× Read By Type discovery → all CCCDs
   enabled → onboarding proceeds normally.

`_sc_flush_done` one-shot flag ensures only Connection 1 is flushed.

### Connection detection bug (v1.25.4) and fix (v1.25.5)

`_sc_flush` was triggered from `_on_added` (ObjectManager `InterfacesAdded` signal).
`InterfacesAdded` fires only when BlueZ creates a **new** Device1 object. iOS uses RPA
(Random Private Address); after the first connection BlueZ caches the Device1 object for
that address → `InterfacesAdded` silently skips on reconnect → `_sc_flush` never fires.

**Confirmed from capture `mock-geberit-mera_btmon_2026-06-21_16-37.btsnoop`:** one iOS
connection (RPA `67:10:94:...`), no "[SC flush]" log line, 22-second ATT timeout.

**Fix (v1.25.5):** `bus.add_message_handler(_on_props_msg)` receives every D-Bus signal.
When `org.bluez.Device1` → `Connected = True` fires, `_on_device_connected()` is called
reliably for every connection regardless of address type. `_on_added`/`_on_removed` are
kept as complementary fallback. Deduplication: `if _connected: return` in
`_on_device_connected`.

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
1. Advertising: company `0x0100`, 11-byte payload — `[state_A=0x00, fw_byte=0x00, state_B] + article(5) + rs_fw(3)`.
   **`state_B` must be dynamically updated to `0x01` when the button is "pressed"** via advertisement
   unregister + re-register. The iOS app reads `byte[2]` of the manufacturer payload to determine
   `IsButtonPressed`; it will not connect until this byte is `0x01`.
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
11. Handle `0x0020` read (UUID 0x2A00 Device Name) → `b"ro"` — secondary confirmation that button is held during Connection 1; mock sets adapter alias to `"ro"` ✓
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

---

## `Connect()` state machine — app source analysis (v2.14.1, 2026-06-25)

### Full Connect() flow

`GeberitDeviceCoreService.Connect(IBleProduct, CancellationToken, executePostConnectTasks:bool)`
is an async state machine. Steps in order:

1. **Disconnect** — if a live device exists (`m__E006 != null`), disconnect it first.
2. **Subscribe** `ConnectionStateChanged` on the BLE product.
3. **`await EstablishAsync(_E004)`** — runs the full init sequence (GATT setup through
   GetFilterStatus). On the real device this takes ~5.7 s for the second connection.
4. **Check `ConnectionState == Ready`** — if NOT Ready → unsubscribe → break →
   line 257: `result = (_E007 != null) ? Fail : Fail` (device never created).
5. **Factory: `_E007 = _E003(BleProduct)`** — creates `NewAquaCleanDevice` (or `OldAquaCleanDevice`
   for DeviceSeries=248, or base `GeberitDevice` for other/unknown DeviceSeries).
6. **`await SaveLastConnectInfo(DeviceId, DateTime.UtcNow)`** — saves to local storage.
   `DeviceId` is built from `BleProductExtension._E000(product)`. Fast (<1 ms).
7. **`m__E006 = _E007`** — stores device reference. Background poll can now use it.
8. **`if (executePostConnectTasks) await m__E006.PostConnectTask(token, onProgress)`**
   — see below.
9. **Result** — `(exception caught at step 8) → TryResult.Fail`. Otherwise:
   `_E007 != null ? TryResult.Success(_E007) : TryResult.Fail`.

**Important**: `m__E006` is stored at step 7 BEFORE PostConnectTask. Even if PostConnectTask
throws (step 8 → exception catch), `m__E006` is non-null. Background polling tasks that hold
a reference to `m__E006` keep running after the failure. This explains why GATT polls continue
on the mock even after the "Fehler" popup.

### DeviceSeries routing in the factory

`DeviceSeries` comes from the **`DeviceStatusData` notification** (CommandId=0xE0=224) that
the device sends spontaneously. Parsed by the frame handler → `DeviceStatusChangedEventArgs`:

| Byte | Field |
|------|-------|
| 0 | version (must be 2) |
| 1 | DeviceSeries (250 = Mera/AcSela/etc., 248 = OldAquaClean) |
| 2 | DeviceVariant (0x0D = Mera Comfort) |
| 3–5 | DeviceNumber (3 bytes LE) |
| 6–9 | DeviceUniqueId |
| 10 | DeviceModel (lower 4 bits) |
| 11–12 | IdcHash |
| 13–14 | Flags |

The factory routes on DeviceSeries:
- `250` → `NewAquaCleanDevice` (Mera Comfort, Sela, etc.)
- `248` → `OldAquaCleanDevice`
- else → base `GeberitDevice`

**DeviceStatusData is NOT sent by the real device during normal onboarding** —
confirmed from `nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard-2.md`: no frame
with CommandId=0xE0 appears anywhere in Connection 2 (the nRF52840 sniffer captures all
spontaneous notifies as "Orphan Notify" entries — if 0xE0 existed it would be there).
Factory routing to `NewAquaCleanDevice` therefore happens via a different path (likely
from proc 0x82 variant byte). The A6 INFO_FRAME burst (`80 01 30 14 0c...`) has
CommandId=0x14 and is a different format. DeviceStatusChangedEventArgs is not triggered.

**`ConnectionState.Ready` is set by InfoFrames received on A6** — not by DeviceStatusData.
See the "Fehler" investigation below for proof.

### PostConnectTask (NewAquaCleanDevice)

Called only when `executePostConnectTasks=True`. Executes four steps:

| Step | Call | DpId | Behavior |
|------|------|------|----------|
| 1 | `ReadDescaleStatisticsFromDevice()` | `DP_DAYS_UNTIL_NEXT_DESCALING` = 589 | via `IDeviceDataPoints.ReadAsync` |
| 2 | `ReadIsDescalingFlagFromDevice()` | `DP_DESCALING_STATUS` = 585 + `DP_DESCALING_DEVICE_LOCK_STATUS` = 983 | via `IDeviceDataPoints.ReadAsync` |
| 3 | `ReadFilterChangeUsageFromDevice()` | `DP_ODOUR_EXTRACTION_FILTER_USAGE` = 39 (if defined) | via `IDeviceDataPoints.ReadAsync` |
| 4 | `ExecuteCommand(DP_START_USER_SESSION)` | DpId 802 = `DP_START_USER_SESSION` | via `IDeviceDataPoints.ExecuteCommand` |

`DeviceDataPoints._E004` (online guard) = `(m__E003 != null) && (m__E001.Count > 0)`.
`m__E001` (DpId definition dict) is populated at `DeviceDataPoints` construction from
`IBleProduct.DataPointDefinitionList`. If `ConnectionState != Ready` at construction time,
`m__E001` is empty → `_E004 = false` → all four steps are no-ops (no BLE calls).

If `_E004 = true` AND a DpId is in `m__E001`: `IBleProduct.ReadAsync([DataPointAddress])` or
`WriteAsync` is called — these make actual BLE GATT writes and would appear in the mock log.

### `IDeviceDataPoints.ReadAsync` semantics

`ReadAsync<TDataPointType, TReturnValue>(DpId, defaultValue)`:
- If `_E004 = false` → returns without action (no BLE call, no exception)
- If DpId not in `m__E001` → logs warning, returns without exception
- If DpId in `m__E001` → calls `IBleProduct.ReadAsync(list)` → makes BLE request

`ExecuteCommand(DpId)`:
- If `_E004 = false` → no-op
- If DpId not in `m__E001` → logs warning, no-op
- If DpId in `m__E001` → calls `IBleProduct.WriteAsync(item)` → sends BLE write

### PostConnectTask (OldAquaCleanDevice — DeviceSeries 248, Mera Comfort)

Source: app source analysis (v2.14.1) + `mock-geberit-mera_2026-06-25_12-37.log` (Connection 2).

`OldAquaCleanDevice` handles DeviceSeries=248 (Mera Comfort, classic AquaClean protocol).
Its `PostConnectTask` uses direct AquaClean protocol procs — not DpId abstraction.
It runs for the "Save" connection (`executePostConnectTasks=True`) but **not** for
Connection 1 (button detection, `executePostConnectTasks=False`).

**Confirmed sequence** (from mock log + app source analysis):

| Step | Internal call | Proc | Args | Count |
|------|--------------|------|------|-------|
| 1 | `InitializeActiveCommonSettings()` | `0x0A` GetActiveCommonSetting | IDs 2,1,3,4,6,7,5,8,0,9 | ×10 |
| 2 | `WriteSomeValuesToDevice()` | `0x0B` SetActiveCommonSetting | IDs 2,1,3 (Colour, Brightness, Mode) | ×3 |
| 3 | `StoredCommonSettings.ReadFromDevice()` | `0x51` GetStoredCommonSetting | IDs 2,1,3,4,6,7,5,8,0,9 | ×10 |
| 4 | `ReadDescaleStatisticsFromDevice()` | `0x0D` GetSystemParameterList | indices [0–11] | ×1 |
| 5 | `FilterState.ReadFromDevice()` — probe | `0x59` GetFilterStatus | IDs [0–7] | ×1 |
| 5 | `FilterState.ReadFromDevice()` — full | `0x59` GetFilterStatus | IDs [0–11] | ×1 |

**Critical data requirements** for `PostConnectTask` to succeed — confirmed from real device
capture `nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard-mapping-1.md`:

**Confirmed from `onboarding-real-mera_timing.md` (new capture 2026-06-25):**

| Proc | Setting / index | Mock (wrong) | Real device | Consequence of 0 |
|------|-----------------|-------------|-------------|-------------------|
| `0x51` | ID 0 WaterHardness | 0 | **1** | Dashboard segmented control at index `0−1=−1` → `ArgumentOutOfRangeException` → "Fehler" |
| `0x51` | ID 1 Brightness | 0 | 3 | safe (0 = minimum) |
| `0x51` | ID 2 Colour | 0 | 2 (Magenta) | safe (0 = Blue) |
| `0x51` | ID 3 Mode | 0 | 2 (WhenApproached) | safe (0 = Off) |
| `0x51` | ID 4–9 | 0 | 2,0,1,1,0,0 | largely safe |
| `0x0A` | ID 2 AnalShowerPressure | 0 | **2** | Shower pressure display at index −1 → possible crash |
| `0x0A` | ID 1 OscillatorState | 0 | **3** | same risk |
| `0x0A` | IDs 0,3,4,6,7 | 0 | 1,2,2,1,1 | same risk |
| `0x0D` | indices 8,9,10 | absent (9 items) | **also absent** (9 items) | ✅ mock is correct |
| `0x59` | ID 11 | absent (11 items) | **also absent** (11 items) | ✅ mock is correct |

**Critical correction from new capture:** the real device ALSO returns only 9 SPL items
(skipping indices 8/9/10) and only 11 filter IDs. The mock's `_SPL_MERA_INDICES` and
`_proc_59()` item count were already correct. The earlier hypothesis that these caused
`KeyNotFoundException` is refuted.

**Proc `0x0A` is GetActiveProfileSetting, NOT GetActiveCommonSetting** — confirmed from
the new capture: proc 0x0A reads AnalShowerPressure, OscillatorState, LadyShowerPressure,
etc. (profile settings), not orientation light / water hardness (common settings).
The ble-protocol.md label "GetActiveCommonSetting" is incorrect for this proc.

`0x0B` (SetActiveProfileSetting) writes come from the `0x0A` responses: since the mock
returns 0 for all active settings, it writes AnalShowerPressure=0, OscillatorState=0,
LadyShowerPressure=0. These may also trigger dashboard display crashes.

`0x0B` (SetActiveCommonSetting) writes come from the `0x0A` responses: since the mock
returns 0 for all active settings, it writes Colour=0 (Blue), Brightness=0, Mode=0 (Off) —
all valid values, no crash at write time.

---

### GeberitDeviceSeries enum — full table

Numeric values are byte 1 of the `DeviceStatusData` BLE notification (CommandId=0xE0=224),
also used as the `deviceSeries` parameter to the device type dispatcher `_E020._E000()`.

| Value | Enum name | Product family | `GeberitDeviceCoreService` factory |
|---|---|---|---|
| 248 | `AquaClean` | Mera Comfort, classic AquaClean | `_E004` → `OldAquaCleanDevice` |
| 250 | `NewAquaClean` | Newer models (Sela, etc.) | `_E005` → `NewAquaCleanDevice` |
| 252 | `Gam` | Geberit Gam faucets | `_E022` |
| 244 | `MirrorCabinet` | Smart mirror cabinet | `_E023` |
| 239 | `Monolith` | Monolith Plus | `_E024` |
| 253 | `Bob` | Unknown product line | `_E025` (uses `variant` + `model`) |
| 254 | `Nurs` | Healthcare/nursing products | `_E027` |
| 249 | `WcFlush` | Toilet flush systems | `_E028` |
| 247 | `SanitaryFlush` | Urinal/sanitary systems | `_E029` |
| 245 | `Gateway` | Hub/gateway devices | `_E02A` |
| 999 | `App` | Test/dev device | special case |
| 1000 | `Unknown` | Fallback | returns `GeberitDeviceType.Unknown` |
| 1001 | `Iot` | Generic IoT | — |

The full dispatcher is in the device configuration module, method `_E020._E000`:
```csharp
// static GeberitDeviceType _E000(ushort series, ushort variant, int model = -1)
switch (series) {
  case 248: return _E020(variant);       // OldAquaClean / Mera Comfort
  case 250: return _E021(variant);       // NewAquaClean
  case 252: return _E022(variant);
  case 244: return _E023(variant);
  case 239: return _E024(variant);
  case 253: return _E025(variant, model); // only case using model param
  case 254: return _E027(variant);
  case 249: return _E028(variant);
  case 247: return _E029(variant);
  case 245: return _E02A(variant);
  case 999: return GeberitDeviceType.App;
  default:  return GeberitDeviceType.Unknown;
}
```

`_E020` class also holds `private Dictionary<GeberitDeviceType, GeberitDeviceConfig> m__E021`
for reverse lookup from type to device configuration metadata.

---

### Device model class hierarchy

```
IGeberitDevice
 └─ GeberitDevice
      • m__E000: IDeviceConfigurationInfo   (private field, private setter → immutable)
      • static FromBleProduct(IBleProduct, IDeviceConfigurationInfo?) → GeberitDevice
        Creates DeviceConfigurationInfo(Uuid, DeviceUniqueId, DeviceSeries,
          DeviceVariant, defaultName, isDemoModeDevice) if not supplied.
      └─ BaseAquaCleanDevice
           • exposes ConfigurationInfo (inherited)
           ├─ NewAquaCleanDevice            (DeviceSeries 250 — PostConnectTask: DpIds 589/585/983/802)
           └─ OldAquaCleanDevice            (DeviceSeries 248 — PostConnectTask: procs 0x0A/0x0B/0x51/0x0D/0x59)
```

**Factory in `GeberitDeviceCoreService`** (routes by `IBleProduct.DeviceSeries`):

| Method | Series | Output |
|--------|--------|--------|
| `_E003(IBleProduct)` | — | router entry point |
| `_E004(product, info)` | 248 | `OldAquaCleanDevice` wrapping `_E006` base |
| `_E005(product, info)` | 250 | `NewAquaCleanDevice` wrapping `_E006` base |
| `_E006(product, info?)` | other | base `GeberitDevice` |

`_E006` creates the `GeberitDevice` base by calling `m__E001.GetDeviceInfo(uuid)` — retrieves
previously stored `IDeviceConfigurationInfo` from local persistence (Core Data / SQLite).

**DeviceType derivation** (`DeviceConfigurationInfo` constructor):
```csharp
DeviceType = _E020._E000(deviceSeries, deviceVariant);
DeviceSeries = (GeberitDeviceSeries)deviceSeries;
```

**Null Object pattern** — `ConnectedDevice` property in `GeberitDeviceCoreService`:
```csharp
ConnectedDevice => m__E006 ?? NullGeberitDevice.Instance
```
- `NullGeberitDevice`: stub implementing `IGeberitDevice`; `ConfigurationInfo = NullDeviceConfigurationInfo.Instance`
- `NullDeviceConfigurationInfo`: `DeviceType = GeberitDeviceType.Unknown` (sentinel for "no device
  connected"); `string` properties → `string.Empty`; `Color` properties → `Color.Transparent`

---

### AquaCleanProduct — GATT initialization (DeviceSeries 248, legacy)

**Device construction** (`AquaCleanProduct` constructor):
- Hardcodes `DeviceSeries = 248`
- Sets up `Capabilities`, `Flags`, `ArticleNumber`, characteristic arrays `cz`/`da`/`db`/`dc`

**`Initialize` method** — strict 8-characteristic validation:

1. Searches for service UUID `3334429d-90f3-4c41-a02d-5cb3a03e0000`
2. Calls `GetCharacteristic(uuid)` for all 8 UUIDs below; throws `"Bulk transfer characteristic
   missing"` if any returns `null`

| Internal name | UUID (suffix) | Handle (mock) | Role |
|---------------|---------------|---------------|------|
| A1 write | `...a13e0000` | `0x0003` | Request WRITE |
| A2 write | `...a23e0000` | `0x0006` | CONS continuation WRITE |
| A3 write | `...a33e0000` | — | (not used in practice) |
| A4 write | `...a43e0000` | — | (not used in practice) |
| A5 notify (`cz`) | `...a53e0000` | `0x000F` | Primary response channel + bridge InfoFrame |
| A6 notify (`da`) | `...a63e0000` | `0x0013` | InfoFrame burst → `ConnectionState.Ready` |
| A7 notify (`db`) | `...a73e0000` | `0x0017` | CONS overflow channel |
| A8 notify (`dc`) | `...a83e0000` | `0x001B` | CONS overflow channel |

3. Enables notifications on `cz`, `da`, `db`, `dc` with a 4000 ms timeout.

CONS channel rotation: A6 (byte1=`0x02`) → A7 (`0x04`) → A8 (`0x06`) → A5 (`0x00`).

**The BlueZ GATT Read-By-Type bug prevents iOS from discovering all 8 characteristics**
(see section below), causing `Initialize` to throw "Bulk transfer characteristic missing" and
preventing `ConnectionState.Ready` — which in turn causes `Connect()` to return `TryResult.Fail`.

---

### BaseProduct / ConnectionState state machine

**Architecture:** Geberit product code **observes** state changes from the underlying Arendi BLE
library but does not control them. No direct `PeripheralState` assignments exist in any
Geberit product file — state transitions are driven entirely by the library.

**Key methods and properties:**

| Name | Description |
|------|-------------|
| `ConnectionState` property | Auto-implemented, protected setter |
| `EstablishAsync(token)` | Delegates to `base.EstablishAsync(token)`; unwraps `ComLibException` |
| `c(object, PeripheralStateChangedEventArgs)` | Event handler from Arendi library |
| — (guard) | Only transitions if `!UpdateInProgress` |
| `SetState(d(b.PreviousState), d(b.NewState))` | Core state transition call |
| `SetState(prev, next)` | Updates `ConnectionState` + fires `ConnectionStateChangedEventArgs` |
| `IsConnected` | `true` when `ConnectionState` is `Ready`, `Initialize`, or `Update` |
| `d(PeripheralState a)` | Switch expression mapping `PeripheralState` → `ConnectionState` |
| re-sync path | Re-syncs after firmware update completes (`UpdateInProgress` → `false`) |

**State progression:** Idle → EstablishLink → Negotiations → DiscoveringServices → Initialize → **Ready**

**`PeripheralState.Ready` maps directly to `ConnectionState.Ready`** (line 912 switch).

**`ConnectionState.Ready` is the precondition for all BLE operations.** Guards confirmed in multiple places:
- `this.f.State != PeripheralState.Ready` (Arendi internal BLE state check)
- `base.State != PeripheralState.Ready` (Ble20Product path)
- `base.ConnectionState == ConnectionState.Ready` (Ble20Product path)

**What triggers Ready on Mera Comfort:**
The Arendi library fires `PeripheralStateChangedEventArgs(NewState=Ready)` after
`Initialize()` completes successfully. `Initialize()` validates the 8 characteristics and
enables notifications — it "succeeds" when the A6 InfoFrame burst is received (the library
treats the first incoming notification as "initialization confirmed"). This is why the mock
must send the InfoFrame burst on **A6** (not A5): the Arendi library only transitions to
Ready after receiving a notification on the A6 characteristic.

---

### BlueZ 5.77 GATT Read-By-Type bug — characteristic discovery failure on iOS

Source files: `bluez-5.77/src/shared/gatt-server.c`, `bluez-5.77/src/shared/gatt-db.c`
(in `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-home-app/bluez-5.77/src/shared/`)

**Symptom:** iOS discovers only 1 of 7 Geberit service characteristics (UUID `0x3A2B` only);
macOS discovers all 7.

**What is NOT the bug:** `gatt-db.c` `foreach_in_range` correctly queues all 7 characteristics
for each Read-By-Type request. The database layer is correct.

**The actual bug:** `process_read_by_type` in `gatt-server.c` receives the full 7-item queue
but cannot pack attributes with **different `item_len` values** into a single PDU response:
- Characteristic `0x3A2B`: `item_len = 7` bytes
- Characteristics A5, A6, A7, A8, A1, A2: `item_len = 21` bytes

The packing loop stops at the `item_len` size boundary and sends only the first same-size
group. iOS receives only the `0x3A2B` entry and stops querying. macOS (MTU=515) compensates
by issuing multiple continuation queries — eventually collecting all 7 characteristics.

**Read-By-Type ranges triggered by iOS:**

| Range | Characteristics queued | What iOS receives |
|-------|----------------------|-------------------|
| `[0x0015–0x0027]` | 7 (`0x3A2B` + A5/A6/A7/A8/A1/A2) | only `0x3A2B` (item_len=7 group) |
| `[0x0021–0x0027]` | 3 (A6/A7/A8 or similar subset) | 0 (all item_len=21) |
| `[0x0024–0x0027]` | 2 (A7/A8 or similar) | 0 |

Result: iOS calls `GetCharacteristic(a53e0000)` → `null` → `Initialize` throws
"Bulk transfer characteristic missing" → connection fails before any proc.

**Fix location:** `process_read_by_type` packing loop in `gatt-server.c` — must handle
multiple item sizes in one response or properly signal continuation for each size group.

---

### Ble20Product Initialize sequence (Alba / Ble20 — series 245, reference only)

**Not Mera Comfort.** Documented here for reference — this is the Alba/Ble20 device path.

**`Initialize` override:**

| Step | Action | Exception on failure |
|------|--------|----------------------|
| 1 | Find service UUID `"FD48"` or `"559eb000-2390-11e8-b467-0ed5f89f718b"` | `"Service not found"` |
| 2 | `GetCharacteristic(an/ao/ap)` → ca (rx), cb (tx), cc (capabilities) | `null` check |
| 3 | Validate: ca must be `Readable`, cb must be `Writeable` | `"Rx not readable or tx not writable"` |
| 4 | `ChangeNotification(enable: true)` on cb | — |
| 5 | `bv.DataPointInventory(cancellationToken, bh)` — reads device parameter structure | — |
| 6 | `bv.ListInventory(cancellationToken)` (series 245 only) | — |
| 7 | `DataPointHelper.ReadRsTsVersion` → `RsVersion`, `TsVersion` | — |
| 8 | `SynchronizeDateTime` — syncs device RTC | — |
| 9 | Return `null` = success, no firmware update needed | — |

All subsequent Ble20Product operations check `!base.IsInRecoveryMode` before executing.

---

## "Fehler / Ein Fehler ist aufgetreten" — root cause identified and fixed (2026-06-25)

**Partially fixed across two versions:**
- `v1.61.0b1`: Fixed InfoFrame burst on A6 → `ConnectionState.Ready` (see below).
- `v1.63.0b1` (planned): Fix `GetStoredCommonSetting` and `GetActiveProfileSetting` returning all-zeros.

See also `docs/developer/mock-geberit-mera.md` "Error popup" section.

Capture used for analysis: `nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard-2.md`
(decoded from `nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard.pcapng` using `nrf-ble-analyze`).

### nRF capture completeness — proof

The `nrf-ble-analyze` script captures **all** BLE RF frames, including spontaneous
device-initiated notifies. These appear as **"Orphan Notify"** entries in the `.md` output
(a notify not preceded by a WRITE on the same characteristic within the expected response window).

Confirmed Orphan Notify entries in Connection 2 of the real device capture:

| Line | Time | Channel | Content |
|------|------|---------|---------|
| 60–65 | t=69.1s | A6 | 6× InfoFrame burst (after CCCD-A7 enable) |
| 83–85 | t=69.2s | A6 | 3× InfoFrame burst (after CCCD-A8 enable) |
| 114 | t=70.0s | A8 | `16 0b 30 37 16 00 0c 30 37 12 00...` |
| 141–142 | t=70.3s | A8 | `16 00 00 00...` × 2 |
| 180–181 | t=79.9s | A5 | `70 00 0c 18...` and `11 05 00 00 07 62 cd...` |
| 205 | t=81.1s | A5 | `11 05 00 00 07 ae 09 00 01 01 07 02 00...` |

**The `.md` file is authoritative and complete.** No need to re-run tshark or nrf-ble-analyze
on the original pcapng — the Orphan Notify entries prove all spontaneous frames are captured.

### The 0.6-second gap — confirmed empty

Lines 440–444 of the real device capture (t=94.0–94.6s): the gap between GetFilterStatus
completion and State Poll #2 contains **no Orphan Notify entries**. There is no spontaneous
frame sent by the real device at the moment the "Fehler" popup would appear on the mock.

### Debunked hypotheses

| Hypothesis | Status | Evidence |
|---|---|---|
| FilterStatus id=3/id=6 non-zero | ✗ error persists after v1.59.0b1 fix | mock log |
| FilterStatus id=4/id=8 timestamps non-zero | ✗ error persists after v1.60.0b1 fix | mock log |
| **DeviceStatusData (0xE0) missing** | **✗ DEBUNKED** | No 0xE0 frame anywhere in real device capture |

The DeviceStatusData hypothesis was based on source code analysis showing `DeviceSeries`
comes from CommandId=0xE0. However the nRF capture proves the real device never sends 0xE0
during onboarding. The factory routing to `NewAquaCleanDevice` must happen another way.

### Observed mock timeline

| Time | Event |
|------|-------|
| 08:47:29 | User clicks "Save" in Geberit Home App |
| 08:47:35.6 | "Verbindung wird hergestellt" screen appears |
| 08:47:36 | Second BLE connection: proc 0x82 starts |
| 08:47:43 | Second connection init completes (proc 0x59 done) |
| **08:47:44** | **"Fehler / Ein Fehler ist aufgetreten" popup** |
| 08:47:46 | GetSystemParameterList (background poll resumes) |
| 08:47:47 | Proc 0x55, then 0x0D |
| 08:47:49 | Proc 0x59 (second FilterStatus) |

The post-error mock sequence is IDENTICAL to the real device post-0x59 sequence. The BLE
connection stays alive; background polling continues. This means `m__E006` (device reference,
stored at step 7 of `Connect()`) IS set — it is `ConnectionState.Ready` (step 4 check) that
fails, not the procs themselves.

### Real device after GetFilterStatus

| Step | Real device | Mock (post-error) |
|------|------------|-------------------|
| +0.6s | 0x0D (State Poll #2) | 0x0D at +3s |
| +0.8s | 0x55 → `00` | 0x55 at +4s |
| +1.1–1.6s | 0x0D × 2 | 0x0D |
| +1.8s | 0x59 (FilterStatus #2) | 0x59 at +6s |

### Root cause 1 (fixed v1.61.0b1): InfoFrame burst on A5 instead of A6

`GeberitDeviceCoreService.Connect()` checks `ConnectionState == Ready` after `EstablishAsync()`
returns (step 4 in the state machine above). `ConnectionState` is set to `Ready` when InfoFrames
are received on **A6** — not A5.

**Evidence:**
- Real device: 9× InfoFrame burst on **A6** at t=69.1–69.2s (immediately after CCCD-A7/A8 enables).
  No error occurs. `ConnectionState = Ready` by the time procs start.
- Mock v1.60.0b1: burst on **A5** only (wrong characteristic). Procs all succeed (independent of
  `ConnectionState`), but step 4 check finds `ConnectionState != Ready` → `TryResult.Fail` → error.

**Why the procs still run after the error:** `m__E006` is stored at step 7 (BEFORE PostConnectTask).
Even when `Connect()` returns `TryResult.Fail`, the background poller already holds `m__E006` and
continues running. This is why the mock log shows 0x0D/0x55/0x59 continuing after "Fehler".

### Root cause 2 (fix planned v1.63.0b1): OldAquaCleanDevice PostConnectTask data mismatch

After v1.61.0b1 fixed `ConnectionState.Ready`, `PostConnectTask` now runs to completion, but
`OldAquaCleanDevice.PostConnectTask` receives incorrect data from the mock, causing the
app to crash or show "Fehler" during dashboard initialization.

Two confirmed data mismatches (verified against `onboarding-real-mera_timing.md`):

1. **`GetStoredCommonSetting` (proc 0x51) — WaterHardness=0 (invalid)**
   Mock returns `bytes([0, 0])` for all stored common settings. Real device returns
   WaterHardness(ID 0)=1. When the dashboard segmented control tries to display
   WaterHardness, it computes `selectedIndex = value - 1 = 0 - 1 = -1` →
   `ArgumentOutOfRangeException` → "Fehler" ~0.8s after `PostConnectTask` completes.

2. **`GetActiveProfileSetting` (proc 0x0A) — all-zeros (invalid)**
   Mock returns `bytes([0, 0])` for all active profile settings. Real device returns
   AnalShowerPressure(ID 2)=2, OscillatorState(ID 1)=3, etc. These values are written
   back via 0x0B and displayed in the settings UI.

**What was confirmed correct (new capture, no fix needed):**
- `GetSystemParameterList` (0x0D): real device returns 9 items `[0,1,2,3,4,5,6,7,11]` — exactly matches mock `_SPL_MERA_INDICES`. No change needed.
- `GetFilterStatus` (0x59): real device returns 11 items (IDs 0–10) — matches mock. No change needed.

**Timeline confirming root cause 2:**

| Time | Event |
|------|-------|
| 12:39:16 | `GetSystemParameterList` (9 items) ✅ mock correct |
| 12:39:17 | `GetFilterStatus` (11 items) ✅ mock correct |
| 12:39:17.8 | **"Fehler"** — 0.8 s after `PostConnectTask` finishes |
| 12:39:21 | Background polling starts (0x0D, 0x55, 0x59) — `m__E006` still live |

The 0.8 s gap = navigation animation (0.3 s) + dashboard UI init crash.

**Fix in v1.63.0b1:**
1. `_proc_51(args)` — return ID-specific values from real device capture:
   WaterHardness(0)=1, Brightness(1)=3, Colour(2)=2, Mode(3)=2, id4=2, id5=0, id6=1, id7=1, id8=0, id9=0
2. `_proc_0a(args)` — return ID-specific values from real device capture:
   id0=1, id1=3, id2=2, id3=2, id4=2, id5=0, id6=1, id7=1, id8=0, id9=0
3. Proc 0x55 response: return value 0 (not registered) instead of 1

---

### Fix v1.61.0b1 — A6 InfoFrame burst

`_send_info_frame_burst()` (renamed from `_send_a5_info_frames`) now sends:

1. **10× on A5** — fires when CCCD-A5 is written (keeps bridge `wait_for_info_frames_async` happy)
2. **9× on A6** — fires once CCCD-A6 is set (~200 ms later, already set by time A5 burst completes)

The `_a6_burst_done` event blocks proc responses during both bursts.

---

## Write characteristics — all four required (fixed in mock v1.40.0b1)

The app checks all four write channels immediately after
GATT discovery: **a1, a2, a3, a4** (all WRITE_WITHOUT_RESPONSE). If any is null, it throws
"Bulk transfer characteristic missing" and shows "connection could not be established"
before writing a single CCCD.

The mock has all four (A1–A4) since v1.40.0b1. All four dispatch to `_handle_request`
identically — only A1 and A2 are actively used by the real device in practice.
