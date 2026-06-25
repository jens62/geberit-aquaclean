# Onboarding Protocol Comparison: Real Mera Comfort vs Mock

**Captures used:**
- Real device: `nRF-sniff-Geberit-Home-App-2.14.1-real-mera-onboard-1.md` (iOS v2.14.1, HB2304EU298413, RS146.21)
- Mock: `onboard-Geberit-Home-App_against_mera-mock_v1.65.0b1_1.md` (mock v1.65.0b1, Ubuntu/BlueZ)

Both decoded with `tools/nrf-ble-analyze.py` using bidirectional ATT traffic mode.

---

## Summary of differences

| # | Topic | Real device | Mock | Severity |
|---|-------|-------------|------|----------|
| 1 | Proc 0x07 node 0x02 value | **0x0200** | ~~0x0400~~ | **BUG — fixed in v1.67.0b1** |
| 2 | GetDeviceInitialOperationDate | **Called** (t≈96s) | Not called | **MISSING** |
| 3 | Proc 0x55 registration level | 0x00 (not registered) | 0x00 (reverted in v1.68.0b1) | ✅ fixed |
| 4 | SC-flush CTRL 0x80 frames | None | Many — from mock timer | Mock behavior only |
| 5 | Proc 0x07 retries per node | 1× per node | 3× per node | Side-effect of SC-flush |
| 6 | BLE connection count | 2 connections | 3 connections | Side-effect of SC-flush |
| 7 | SAP / serial number | HB2304EU298413 | HB2300EU00000 | Placeholder |
| 8 | ATT MTU | client=185, server=23 | client=517, server=517 | Platform difference (Linux) |
| 9 | GetStoredProfileSetting values | See table | **Match** | — |
| 10 | GetStoredCommonSetting values | See table | **Match** | — |
| 11 | GetDeviceRegistrationLevel | 0x00 | 0x00 (was 0x01 in v1.66) | Reverted = correct |

---

## Confirmed bug: proc 0x07 node 0x02 wrong value

**Fixed in mock v1.67.0b1.** `_PER_NODE_PROFILE_SETTINGS[0x02]` was `4`; should be `2`.

Real device GetPerNodeProfileSetting results (Connection 1, t=79.6s–81.1s):

| Node | Real result | Mock result (v1.65) | Match? |
|------|-------------|---------------------|--------|
| 0x04 | 0x0200 (2) | 0x0200 (2) | ✅ |
| 0x02 | 0x0200 (2) | 0x0400 (4) | ❌ **bug** |
| 0x06 | 0x0400 (4) | 0x0400 (4) | ✅ |
| 0x05 | 0x0100 (1) | 0x0100 (1) | ✅ |
| 0x03 | 0x0100 (1) | 0x0100 (1) | ✅ |
| 0x09 | 0x0100 (1) | 0x0100 (1) | ✅ |
| 0x01 | 0x0100 (1) | 0x0100 (1) | ✅ |
| 0x00 | 0x0100 (1) | 0x0100 (1) | ✅ |
| 0x0d | 0x0100 (1) | 0x0100 (1) | ✅ |
| 0x08 | 0x0300 (3) | 0x0300 (3) | ✅ |
| 0x07 | 0x0000 (0) | 0x0000 (0) | ✅ |

The mock calls each node 3× per connection (vs 1× on the real device) due to the
SC-flush retry cycle — but returns the correct value on each attempt.

---

## Missing: GetDeviceInitialOperationDate (proc 0x86)

Real device Connection 2 sequence after GetStoredCommonSetting:
```
t=93.8s  GetSystemParameterList → SPL result
t=94.0s  GetFilterStatus → filter result
t=94.6s  GetSystemParameterList (2nd)
t=94.8s  GetDeviceRegistrationLevel → 0x00
t=95.1s  GetSystemParameterList (3rd)
t=95.6s  GetSystemParameterList (4th)
t=95.8s  GetFilterStatus (2nd)
t=96.0s  GetDeviceInitialOperationDate → FIRST[1] with 1 CONS frame
t=96.1s  GetSystemParameterList (polling continues)
```

Mock Connection 3 sequence after GetStoredCommonSetting:
```
t=138.3s GetSystemParameterList → SPL result
t=139.5s GetFilterStatus → filter result
t=142.2s GetSystemParameterList (2nd)
t=143.4s GetDeviceRegistrationLevel → 0x00
t=143.4s GetSystemParameterList (3rd)
t=144.6s GetFilterStatus (2nd)
t=145.7s GetSystemParameterList (4th)
[NO GetDeviceInitialOperationDate — capture ends t=148.1s]
```

The mock capture was recorded for ~5s after proc 0x55 with no proc 0x86 request —
on the real device, proc 0x86 was called ~1.1s after proc 0x55. The app appears
not to request GetDeviceInitialOperationDate from the mock. Likely cause: the
incorrect proc 0x07 node 0x02 value (4 vs 2) puts the app into a different code
path during onboarding. **To verify: retest with v1.67.0b1.**

---

## WRONG: proc 0x55 registration level fix (v1.66.0b1)

The v1.66.0b1 change set `_registration_level = 1` (Private Device) as default,
making mock proc 0x55 return 0x01 instead of 0x00.

**This is incorrect.** The real device returns `result=00` at t=94.9s during a
fresh onboarding. Both real device and mock should return 0x00 for proc 0x55.
The app does not show "Fehler" when it receives 0x00 for proc 0x55 — it's the
expected "not yet registered" state.

**Reverted in v1.68.0b1** — `_registration_level` reset to 0 (matches real device).

---

## SC-flush behavior

The mock sends spontaneous CTRL byte0=0x80 frames from the device to the app
while building multi-frame responses. These are a side-effect of the mock's
timer-based response queuing (BluezPeripheral internal send buffer).

Real device: zero CTRL 0x80 frames observed.

Consequences of SC-flush in mock:
1. App retries each proc 0x07 request 3× (1-second timeout cycle) instead of 1×
2. Extra BLE reconnections — mock has 3 BLE connections, real device has 2
3. Slow overall onboarding — ~150s for mock vs ~30s for real device

The SC-flush is a known limitation of the BlueZ/BluezPeripheral stack.

---

## Procedures that match

All the following procedures return identical values in mock and real device:

### GetStoredProfileSetting (proc 0x53)

| Setting | Index | Real result | Mock result |
|---------|-------|-------------|-------------|
| AnalShowerPressure | 0 | 0x0200 (2) | 0x0200 ✅ |
| OscillatorState | 1 | 0x0300 (3) | 0x0300 ✅ |
| LadyShowerPressure | 2 | 0x0200 (2) | 0x0200 ✅ |
| AnalShowerPosition | 3 | 0x0200 (2) | 0x0200 ✅ |
| WaterTemperature | 4 | 0x0100 (1) | 0x0100 ✅ |
| WcSeatHeat | 5 | 0x0100 (1) | 0x0100 ✅ |
| LadyShowerPosition | 6 | 0x0000 (0) | 0x0000 ✅ |
| DryerTemperature | 7 | 0x0000 (0) | 0x0000 ✅ |
| OdourExtraction | 8 | 0x0100 (1) | 0x0100 ✅ |
| DryerState | 9 | 0x0000 (0) | 0x0000 ✅ |

### SetStoredProfileSetting (proc 0x54) — app writes

Both captures show the app writing the same three values post-read:
- AnalShowerPressure = 2
- OscillatorState = 3
- LadyShowerPressure = 2

### GetStoredCommonSetting (proc 0x51)

App queries IDs in order: 2, 1, 3, 4, 6, 7, 5, 8, 0, 9

| ID | Name | Real result | Mock result |
|----|------|-------------|-------------|
| 2 | Color | 0x0200 (2=Magenta) | 0x0200 ✅ |
| 1 | Brightness | 0x0300 (3) | 0x0300 ✅ |
| 3 | Activation | 0x0200 (2=WhenApproached) | 0x0200 ✅ |
| 4 | LidSensorRange | 0x0200 (2) | 0x0200 ✅ |
| 6 | LidAutoOpen | 0x0100 (1) | 0x0100 ✅ |
| 7 | LidAutoClose | 0x0100 (1) | 0x0100 ✅ |
| 5 | OdourExtractionRunOn | 0x0000 (0) | 0x0000 ✅ |
| 8 | AutoFlush | 0x0000 (0) | 0x0000 ✅ |
| 0 | WaterHardness | 0x0100 (1) | 0x0100 ✅ |
| 9 | DemoMode | 0x0000 (0) | 0x0000 ✅ |

### GetDeviceRegistrationLevel (proc 0x55)

| | Real device | Mock (v1.65) | Mock (v1.66) |
|-|-------------|--------------|--------------|
| result | 0x00 (Not registered) | 0x00 ✅ | 0x01 ❌ (wrong fix) |

### GetSOCApplicationVersions (proc 0x81)

Both return: `result = 31 30 12 00` (RS30 / TS 18 encoded)

### SubscribeNotif handshake

Both captures show 4× SubscribeNotif_0x11 then 4× SubscribeNotif_0x13 per
connection. Response pattern: FIRST[3] for first 3, FIRST[1] for 4th.

---

## BLE link layer differences

| Parameter | Real device | Mock |
|-----------|-------------|------|
| ATT MTU (client/server) | 185 / 23 | 517 / 517 |
| BLE PHY | 1M | 2M (iOS negotiates up) |
| Encryption | None (SMP unencrypted) | None |
| SAP number | HB2304EU298413 | HB2300EU00000 |
| Serial number | actual | placeholder zeros |

The MTU difference does not affect correctness — both sides use 20-byte ATT
payloads matching the A5 notification characteristic MTU.

---

## Onboarding procedure order

Both captures follow the same high-level procedure sequence per BLE connection:

```
CCCD disable sequence (A5/A6/A7/A8)
GetDeviceIdentification (proc 0x82)
GetNodeList (proc 0x85)
GetSOCApplicationVersions (proc 0x81)
GetFirmwareVersionList (proc 0x0E) — IDs [1,3,4,5,6,7,8,9]
GetFirmwareVersionList (proc 0x0E) — ID [15]
SubscribeNotif_0x11 ×4
SubscribeNotif_0x13 ×4
GetPerNodeProfileSetting (proc 0x07) ×11 nodes  ← only Connection 1 / "button ceremony"
GetStoredProfileSetting (proc 0x53) ×10 settings
SetStoredProfileSetting (proc 0x54) ×3 writes
GetStoredCommonSetting (proc 0x51) ×10 settings
GetSystemParameterList (proc 0x0D)
GetFilterStatus (proc 0x59)
GetDeviceRegistrationLevel (proc 0x55)
GetDeviceInitialOperationDate (proc 0x86)  ← real device only
[polling loop: GetSystemParameterList + GetFilterStatus alternating]
```

---

## Open investigations

- **Why does the app skip GetDeviceInitialOperationDate on the mock?** The proc 0x07
  node 0x02 bug (value 4 vs 2) is the prime suspect. Retest with v1.67.0b1.
- **proc 0x55 reverted in v1.68.0b1:** real device returns 0x00; mock now matches.
- **"Fehler" popup:** identified as proc 0x51 WaterHardness=0 in earlier analysis
  (see GATT findings memory). Fixed in v1.65.0b1 (WaterHardness now returns 1).
  Confirm no Fehler after v1.67.0b1 test.
