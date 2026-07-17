# Firmware Version — Implementation Status & Probe Results

## Overview

Two separate procedures exist for reading firmware/version data from the AquaClean device:

| Procedure | Name | Status |
|-----------|------|--------|
| `0x81` | `GetSOCApplicationVersions` | CallClass + full wiring exists; response received but **not parsed** (raw hex) |
| `0x0E` | `GetFirmwareVersionList` | CallClass created; fully wired (REST, CLI, MQTT, webapp); **probe results: dead end** |

---

## What already works

### `GetSOCApplicationVersions` (0x81)

The full stack is wired:

| Layer | File | Status |
|-------|------|--------|
| CallClass | `aquaclean_core/Api/CallClasses/GetSOCApplicationVersions.py` | ✅ Sends correctly |
| BaseClient method | `aquaclean_core/Clients/AquaCleanBaseClient.py` | ✅ `get_soc_application_versions_async()` |
| High-level client | `aquaclean_core/Clients/AquaCleanClient.py` | ✅ Called in `connect()`; cached in `soc_application_versions` |
| REST endpoint | `RestApiService.py` + `main.py` | ✅ `GET /data/soc-versions` |
| CLI command | `main.py` + `__main__.py` | ✅ `--command soc-versions` |
| MQTT publish | `main.py` | ✅ `{topic}/peripheralDevice/information/SocVersions` |
| Webapp | `static/index.html` | ✅ "SOC Versions" button + display |

**The response IS received from the device.** Example: `31301200` (4 bytes raw hex). The only missing piece is parsing:

```python
# GetSOCApplicationVersions.py — result() method
def result(self, data):
    logger.info("Not yet fully implemented.")
    readable_data = ''.join(f'{b:02X}' for b in data)
    # Deserializer call is commented out:
    # ds = Deserializer.Deserializer()
    # di = ds.deserialize(SOCApplicationVersion.SOCApplicationVersion, data)
    return readable_data  # Returns raw hex string, e.g. "31301200"
```

The DTO stub (`Dtos/SOCApplicationVersion.py`) only has placeholder fields `A: bytes`, `B: bytes` — no actual field mapping.

**Observed value: `31301200`** — this does NOT decode to "RS28.0 TS199" (the firmware string visible in the official Geberit Home app) under any obvious encoding (ASCII, BCD, etc.). The official firmware string likely comes from a different procedure.

### MQTT in persistent mode: empty handler

In `ServiceMode`, the `SOCApplicationVersions` event handler is an empty stub:

```python
# main.py ~line 1256
async def soc_application_versions(self, sender, args):
    pass  # No MQTT publish in persistent mode
```

On-demand mode publishes correctly (via the REST → MQTT bridge in `get_soc_versions()`).

---

### `GetFirmwareVersionList` (0x0E) — Wired but probe is a dead end

The full stack is wired as of commit `deb2450`:

| Layer | File | Status |
|-------|------|--------|
| CallClass | `aquaclean_core/Api/CallClasses/GetFirmwareVersionList.py` | ✅ Configurable payload; returns raw hex + ASCII |
| BaseClient method | `aquaclean_core/Clients/AquaCleanBaseClient.py` | ✅ `get_firmware_version_list_async(payload)` |
| High-level client | `aquaclean_core/Clients/AquaCleanClient.py` | ✅ Fetched in `connect()`; `FirmwareVersionList` event |
| REST endpoint | `RestApiService.py` + `main.py` | ✅ `GET /data/firmware-version-list?payload=<hex>` |
| CLI command | `main.py` + `__main__.py` | ✅ `--command firmware-version-list` |
| MQTT publish | `main.py` | ✅ `{topic}/peripheralDevice/information/FirmwareVersionList` |
| Webapp | `static/index.html` | ✅ Button + raw_hex/ASCII display |

**REST API probe interface:**

```bash
# Empty payload (default)
curl -s "http://localhost:8080/data/firmware-version-list"

# With hex payload
curl -s "http://localhost:8080/data/firmware-version-list?payload=0000"
```

---

## Probe results (2026-02-26)

Tested against a live Geberit AquaClean Mera Comfort device:

| Payload (hex) | Bytes sent | Result | Query time | Notes |
|---------------|-----------|--------|------------|-------|
| `(empty)` | 0 bytes | `{"raw_hex":"","ascii":""}` | ~500ms | Empty response |
| `00` | 1 byte | `{"raw_hex":"","ascii":""}` | ~500ms | Empty response |
| `0000` | 2 bytes | **APPLICATION CRASH** | — | Required `bluetoothctl remove <MAC>` to recover |
| `00000000` | 4 bytes | `{"raw_hex":"","ascii":""}` | ~500ms | Empty response |
| `01` | 1 byte | `{"raw_hex":"","ascii":""}` | ~500ms | Empty response |

**Key observations:**

1. **Procedure 0x0E IS recognized by the device** — it responds (doesn't time out or error). The ~500ms response time is consistent with a valid BLE round-trip.
2. **All non-crashing payloads return empty data** — the device sends back zero bytes regardless of what we send.
3. **`payload=0000` (2 bytes) crashed the application** — this is the exact byte count that matches the C# signature `GetFirmwareVersionList(object arg1, object arg2)` where each arg might be 1 byte. The crash suggests the device DID send a non-empty response that our incomplete `result()` method failed to parse (or the BLE stack was upset). A try/except guard is recommended (see below).
4. **No ASCII firmware string returned** — "RS28.0 TS199" does not appear in any response. Either the correct arg values are required first, or this procedure returns structured binary (not ASCII).

**Conclusion: Dead end without BLE sniffing.** The correct `arg1`/`arg2` values cannot be determined by trial-and-error. BLE traffic capture from the official Geberit Home app (while it displays firmware info) is required.

---

## Recommended guard against `payload=0000` crash

The `get_firmware_version_list_async()` call in `AquaCleanClient.connect()` runs at
connection time. If the device sends an unexpected response, the current raw parser
can crash. Add a try/except:

```python
# AquaCleanClient.py — in connect()
try:
    self.firmware_version_list = await self.base_client.get_firmware_version_list_async()
    await self.FirmwareVersionList.invoke_async(self, self.firmware_version_list)
except Exception as e:
    logger.warning(f"GetFirmwareVersionList failed (non-fatal): {e}")
    self.firmware_version_list = None
```

This makes the probe call survivable — a malformed response won't crash the bridge.

---

## Path forward

### Option A — Shelve the probe (recommended)

The `GetFirmwareVersionList` endpoint is now accessible for future experimentation
(`GET /data/firmware-version-list?payload=<hex>`). Shelve active investigation until:
- BLE sniffing the official Geberit Home app is possible, OR
- A developer with the C# reference code can identify the correct arg values

### Option B — Analyse `GetSOCApplicationVersions` response bytes

The `0x81` response of `31301200` (4 bytes) could be parsed differently:
- Try all 4 bytes as a version struct: e.g. major=0x31 (49→'1'), minor=0x30 (48→'0'), patch=0x12 (18), build=0x00
- This doesn't match "RS28.0 TS199" but might be a separate "SOC application version" number
- BLE sniffing remains the only way to identify the full firmware string source

### Option C — BLE sniffing

Using Wireshark + btsnoop or a BLE sniffer (e.g. nRF Sniffer) while the official
Geberit Home app displays the firmware version "RS28.0 TS199":
1. Capture the BLE traffic
2. Identify which procedure + args return the string
3. Implement the correct CallClass

---

## Implementation steps (once format is known)

### Step 1 — Parse `GetSOCApplicationVersions.result()`

Update `aquaclean_core/Api/CallClasses/GetSOCApplicationVersions.py`:

```python
def result(self, data):
    # TODO: parse data bytes into firmware version fields
    # Known: 4-byte response, e.g. 0x31 0x30 0x12 0x00
    return parsed_result
```

Update the DTO `Dtos/SOCApplicationVersion.py` with real fields once byte layout is known.

### Step 2 — Wire into `system-info` via cache

`system-info` is intentionally BLE-free (useful for diagnosing installs). **Do not require BLE for system-info.** Instead:

- Cache parsed firmware version in `device_state` after first successful poll/connect (same pattern as `sap_number`, `serial_number`)
- `system-info` reads from `device_state` cache → returns `null` if not yet polled, populated after first connect

```python
# In system-info data assembly (main.py get_system_info())
"firmware": {
    "soc_versions": device_state.get("soc_versions"),           # null until first connect
    "firmware_list": device_state.get("firmware_version_list"), # null until first connect
}
```

### Step 3 — Full interface update (mandatory)

Per project policy, any new capability must be updated across all interfaces:

- [ ] `device_state` — add `soc_versions` (parsed) and optionally `firmware_version_list`
- [ ] `system-info` CLI command — include firmware fields in output JSON
- [ ] `GET /info/system` REST endpoint — include firmware fields
- [ ] MQTT `{topic}/centralDevice/systemInfo` — already publishes system-info on startup; will pick up automatically
- [ ] `soc_application_versions()` event handler in persistent mode — publish to MQTT
- [ ] HA Discovery — add SOC versions sensor (currently missing from `get_ha_discovery_configs()`)
- [ ] Webapp — update system-info panel to show parsed firmware version instead of raw hex
- [ ] `docs/cli.md` — update `system-info` command response example

---

## iOS app — firmware update check mechanism

Investigated 2026-06-26 from iOS app v2.14.1 behavioral analysis and HAR captures.

### How the app decides to show the "update firmware" prompt

The app uses two firmware services: a **cloud service** (`FirmwareServiceClient`) that
fetches firmware packages from Geberit's cloud, and a **local service** (`FirmwareServiceLocal`)
that loads `.zip` files bundled in the app. Both use identical logic for update decisions —
`FirmwareServiceLocal` is a thin shell that delegates all filtering to the same internal
implementation as the cloud service.

The decision flow after every BLE connect:

1. `ConnectToAquaCleanViewModel` runs a firmware check function immediately after BLE connect.
2. That function calls `product.GetVersion()` — which matches the device's current RS/TS
   against firmware packages using a filter: packages must have a node 0x01 firmware with
   matching RS/TS AND a contract type of `Ble2V1`, `EspV1`, or `GatewayV1`.
3. Since all Mera cloud packages use contract type `AquaCleanV1` (confirmed from HAR),
   the filter never matches → `GetVersion()` **always returns null** for Mera.
4. When `GetVersion()` returns null, the firmware check immediately returns `true` —
   force update necessary — **for every Mera device on every connect**.
5. `FirmwareForceUpdateViewModel` is always navigated to.
6. Inside `FirmwareForceUpdateViewModel.Initialize()`, `GetActiveUpdateAsync()` is called.
   - Cloud path: returns `null` (same `AquaCleanV1` filter → empty list) →
     `ShowGenericError()` → "Fehler" dismissible popup
   - Local bundled path: **unknown** — if the bundled `.zip` files use `Ble2V1` contract
     for RS30.0, a non-null result triggers the **blocking update UI**

### Device identity → DeviceVariant mapping

From `AcDeviceTypeHelper.GetDeviceType(articleNumber, serialNumber)`:

| Article prefix | SAP first char | `AquacleanOldVariant` |
|---|---|---|
| `146.21` | `H` | `AcMeraComfort` |
| `146.21` | `G` | `AcMeraClassic` |
| `146.20` | any | `AcMeraClassic` |
| `146.22`, `243.64`, `243.71` | any | `AcSela` |
| `146.19`, `146.24` | any | `AcMeraFloorstanding` |

Mock article `146.21x.xx.1` + SAP `HB2304EU298414` (starts with 'H') → `AcMeraComfort`.
Real device article `146.21x.xx.1` + SAP `HB2304EU298413` (starts with 'H') → `AcMeraComfort`.

Both map to the same variant — the cloud firmware lookup is identical for mock and real.

### SAP/serial number interpretation — `ProductIdentifier`

The SAP string (`HB2304EU298413`) is used in two ways:

1. **First character check:**
   - starts with `'G'` → `AcMeraClassic`
   - starts with `'H'` (or anything else) → `AcMeraComfort`
   (for article prefix `146.21` only)

2. **CRC32 → `UniqueId`:** the full SAP string is CRC32'd (ASCII) to produce the `UniqueId`
   field. Format: `{Series:X2}{Variant:X2}-0000000[{CRC32:X8}]`.
   This identifier is the app's **unique per-device key** in local storage — it tracks
   onboarding state, connection history, and firmware update flow status.

So:
- `HB2304EU298413` (real device) → CRC32 → unique ID `A` — known to app from prior pairing
- `HB2304EU298414` (mock) → CRC32 → unique ID `B` (different) — unknown to app, fresh state

### HAR-confirmed: active RS30.0 TS206 package NodeFirmware details

Confirmed 2026-06-26 from `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit app installieren und in betrieb nehmen.har`:

Active package: `series=248, variants=[1,2,3], packageVersion=30.0.206.250722, isActive=True, 15 NodeFirmwares`

| NodeId | rsTsVersion | deployContract | RuleSet |
|--------|-------------|----------------|---------|
| 0x01 | 30.206 | AquaCleanV1 | **(empty)** |
| 0x03 | 8.31 | AquaCleanV1 | (empty) |
| 0x04 | 8.37 | AquaCleanV1 | (empty) |
| 0x05 | 11.60 | AquaCleanV1 | (empty) |
| 0x06 | 8.48 | AquaCleanV1 | (empty) |
| 0x07 | 11.41 | AquaCleanV1 | (empty) |
| 0x08 | 9.31 | AquaCleanV1 | (empty) |
| 0x09 | 7.19 | AquaCleanV1 | (empty) |
| 0x0A | 7.18 | AquaCleanV1 | (empty) |
| 0x0B | 8.23 | AquaCleanV1 | (empty) |
| 0x0C | 7.18 | AquaCleanV1 | (empty) |
| 0x0D | 5.9 | AquaCleanV1 | (empty) |
| 0x0D | 1.12 | AquaCleanV2 | (empty) |
| 0x0E | 7.27 | AquaCleanV1 | (empty) |

**Key findings:**

1. **RuleSets are all empty** — no DataPoint predicates. No BLE reads needed for rule evaluation.
2. **All nodes use `deployContract=AquaCleanV1`** — the internal firmware filter requires
   `Ble2V1 || EspV1 || GatewayV1`. Since every Mera cloud node is `AquaCleanV1`, none match.

### `FirmwareServiceLocal` — findings

`FirmwareServiceLocal` is a **thin shell**. It implements `IFirmwareService` but
delegates ALL update logic to the same shared implementation class used by
`FirmwareServiceClient`. The two services differ only in their data source:

| Service | `FirmwareVersions` source |
|---------|--------------------------|
| `FirmwareServiceClient` | Cloud API (`prod.firmwarev1.services.geberit.com`) |
| `FirmwareServiceLocal` | Local `.zip` files bundled in the app |

Both go through identical filtering logic — the update decision is the same code.

Local zip loading: the constructor scans for bundled `.zip` files, reads
`FirmwarePackage.json` from each, marks every package as active with channel = Release.

### Shared firmware update logic

The shared implementation class handles all five `IFirmwareService` methods. Key behaviors:

**`GetProductVersion(product)`** — matches device's RS/TS firmware against firmware list:
- Builds `NodeVersion` from device's current RS/TS values
- Filters packages by `DeviceSeries` + `DeviceVariant`
- For each package (descending by version), checks if any NodeFirmware matches:
  `NodeId == "0x01"` AND `RsTsVersion == device's current` AND contract is `Ble2V1`, `EspV1`, or `GatewayV1`
- No match → **returns `PackageVersion = null`** (always for all Mera AquaCleanV1 packages)

**`GetAvailableUpdatePackagesAsync(product, upgradesOnly)`**:
- Calls `GetProductVersion()` → `PackageVersion = null` for Mera
- Pre-filter passes ALL matching Series/Variant packages when current version is null
- Inner filter still requires `Ble2V1 || EspV1 || GatewayV1` on node 0x01 → **returns empty list**

**`IsUpdateAvailableAsync(product)`** — `.Any()` on empty list → **always `false` for all Mera**.
The firmware update prompt cannot be triggered via this method.

**`GetActiveUpdateAsync(product)`** — returns null for Mera (cloud or any AquaCleanV1 source).

### `ConnectToAquaCleanViewModel` — the firmware check gate

Post-connect flow:

```
IsForceUpdateNecessary = firmwareCheckFunction(connectedDevice)
if IsForceUpdateNecessary:
    navigate to FirmwareForceUpdateViewModel
```

The firmware check function (`_E004` in the app source) runs for every Mera device:

1. `product.GetVersion()` — tries to match device firmware in the **local** (bundled) service
   - Returns non-null only if a bundled `.zip` has node 0x01 with matching RS/TS AND a non-AquaCleanV1 contract
   - For Mera with cloud-only packages: **always null**
2. **If null → immediately return `true` (force update necessary)** — for ALL Mera devices
3. If non-null: proceeds to `AppRemoteSettings.device_api_min_version` check (never reached for Mera)

The `device_api_min_version` field in `AppRemoteSettings` (fetched from Geberit's remote
settings cloud) is therefore **never consulted for Mera** — the function exits at step 2.

`IsForceUpdateNecessary = true` for every Mera on every connect → `FirmwareForceUpdateViewModel`
is always navigated.

### `FirmwareForceUpdateViewModel` — what actually happens

`Initialize()`:

1. Calls `product.GetProductVersion()?.PackageVersion?.ToString()` → `null` → shows `"--"`
2. Calls `GetActiveUpdateAsync()` → result:
   - **`null`** (cloud AquaCleanV1 or if initialize faults) → `ShowGenericError()` → "Fehler" dismissible popup
   - **non-null** (local bundled `.zip` with Ble2V1 contract, non-AquaCleanV1) → **blocking update UI**

### `RemoteSettingsService` — how it works

`AppRemoteSettings` and device remote settings are fetched via anonymous HTTP — no Geberit
cloud login required. Keyed by device type (`"248-3"` for AcMeraComfort). Works like
Firebase Remote Config: public configuration, no auth token.

**Q: No login required — how is the cache filled?**
Anonymous HTTP endpoint on Geberit's remote settings server; result stored in iOS Keychain.

**Q: When is the cache filled?**
During every `ConnectToAquaCleanViewModel` run at the step AFTER BLE connect.
But this step is only reached when `GetVersion()` returns non-null — **never for Mera**.

**Q: What identifier?**
`"248-3"` for `AcMeraComfort`. Type-level, shared across all Mera Comfort devices.

**Q: What happens with no internet?**
HTTP call throws → exception caught → app continues using Keychain cache if available.
Entirely moot for Mera since the remote settings check is never reached.

### Why mock RS28.0 triggers the prompt but real device does not — ROOT CAUSE UNKNOWN

Every Mera device, mock or real, follows the same code path through `ConnectToAquaCleanViewModel`:

```
GetVersion() = null → force update = true → navigate to FirmwareForceUpdateViewModel
→ GetActiveUpdateAsync() → ?
```

The "Fehler" popup (from `ShowGenericError()`) fires when `GetActiveUpdateAsync()` returns
null. The **blocking update UI** fires when it returns non-null — which only happens if the
LOCAL bundled `.zip` files have packages with a non-AquaCleanV1 contract.

**Most likely explanation (hypothesis — cannot verify without iOS app bundle access):**

The app bundles local firmware files for AcMeraComfort with a `Ble2V1` contract
covering RS30.0 TS206 only:

| Scenario | `GetVersion()` | Local `GetActiveUpdateAsync()` | Result |
|----------|----------------|-------------------------------|--------|
| Mock RS28.0 | null (no RS28.0 match in bundled Ble2V1 packages) | non-null (RS30.0 available) | **Blocking update UI** |
| Mock RS30.0 | non-null (RS30.0 exact match found) | not reached (force update = false) | **No prompt at all** |
| Real device RS28.0 | null | `DataPointHelper.ReadRsTsVersion` fails silently for real device → `Initialize()` faults | **No prompt (faulted)** |

**Why real device `ReadRsTsVersion` likely fails:** the app reads firmware version data from
specific GATT data points (not from proc 0x0E). If the real device does not expose those
specific data point IDs, `ReadRsTsVersion` returns empty values, the firmware version
matching in `Initialize()` throws, and no update prompt is shown.

The mock's GATT handler explicitly handles those data point reads; the real device likely
does not (the firmware version displayed in the official app UI probably comes from proc 0x0E
response data, not from GATT data point reads).

**Caveat:** this is not confirmed. The real device's firmware state during testing is also
unknown (it may have already been at RS30.0 when the mock comparison test was run).

**Ruled-out hypotheses:**

| Hypothesis | Ruled out because |
|------------|------------------|
| iOS Bluetooth pairing state | Mera uses zero SMP — no iOS-level BLE pairing |
| `FirmwareServiceCacheData.json` per-device state | Global cache keyed by `ContentId` only |
| DataPoint RuleSet predicates | All 15 Mera NodeFirmwares have empty RuleSets (HAR) |
| `FirmwareServiceLocal` separate AquaCleanV1 path | Delegates to same shared update logic |
| `IFirmwareService.IsUpdateAvailableAsync` via cloud | Always `false` for AquaCleanV1 — confirmed |
| `AppRemoteSettings.device_api_min_version` | Never reached: force-update check returns true before remote settings |
| Feature flag in app update gate | Hardcoded to `true` — no gate effect |

### Practical fix for the mock — SUPERSEDED 2026-07-16, see below

~~**ALL entries in `_FW_COMPONENT_VERSIONS` must return RS30.0 TS206** (fixed in v1.75.0b1).~~

~~Setting only component 1 (main controller) to RS30.0 is **not sufficient** —
confirmed empirically 2026-06-26 by comparing BLE logs from v1.64.0b1 (all RS30.0,
dismissible Fehler only) vs v1.74.0b1 (component 1 = RS30.0, components 3–15 = real
per-device RS07–RS11 → blocking update UI).~~

**Superseded finding (v1.76.0b1, commit `e4295cc`, 2026-07-16):** the "all RS30.0"
workaround above was itself replaced by reporting each component's real, individually
diffed value (component 1 = RS30.0 TS206, components 3–15 = their actual real per-device
RS07–RS11 versions — same shape as the "not sufficient" v1.74.0b1 config above, but with
correctly diffed sub-component values instead of whatever v1.74.0b1 used). **Tested
against the real Geberit Home App (2026-07-15):** the app still requests a firmware
upgrade regardless — consistent with the decompiled finding above that
`IsForceUpdateNecessary` is unconditionally `true` for every Mera connect, independent of
the actual version delta — and resolves to the **dismissible "Fehler" popup**, not the
blocking UI. Confirmed working end-to-end by the user (2026-07-16): "Fehler" appears, "OK"
dismisses it, normal operation continues. This is the mock's current, unchanged default
as of this doc's last edit — **not** something to revert to; it already is the working
baseline.

The mock still has no handler for the actual firmware-update proc sequence
(`ctx=0x40 proc=0x00/0x52/0x53/0x04`, `ctx=0x00 proc=0x01`) — it falls through to
"unknown proc, returning empty OK", so the app loops on the probe sequence for a while
before giving up and showing the dismissible Fehler. Implementing real handlers for that
sequence (decoded in `memory/mera-firmware-update-ble-protocol.md` /
`.claude/rules/ble-protocol.md`) is tracked as
`docs/developer/mock-service-requirements.md` Phase 9b.

**Given this, the root-cause analysis below (why RS28.0 vs RS30.0 differ, and whether the
real device diverges from the mock) should be treated as historical/unconfirmed — it
predates the empirical v1.76.0b1 test above and its conclusions may not hold.**

**Real Mera HB2304EU298413 proc 0x0E response** (source: `onboarding-real-mera.md` lines 763–764):

| Component | v1 v2 build | RS/TS |
|-----------|-------------|-------|
| 1 | `32 38 c7` | RS28.0 TS199 |
| 3 | `30 38 1f` | RS08 TS31 |
| 4 | `30 38 25` | RS08 TS37 |
| 5 | `31 31 3c` | RS11 TS60 |
| 6 | `30 38 30` | RS08 TS48 |
| 7 | `31 31 29` | RS11 TS41 |
| 8 | `30 39 1f` | RS09 TS31 |
| 9 | `30 37 13` | RS07 TS19 |
| 10 | `30 37 12` | RS07 TS18 |
| 11 | `30 37 16` | RS07 TS22 |
| 12 | `30 37 12` | RS07 TS18 |
| 14 | `30 37 1b` | RS07 TS27 |
| 15 | `30 31 00` | RS01 TS0 |

The real device sends RS28.0 TS199 for component 1 and real sub-node versions for 3–15 —
yet does NOT trigger the blocking update UI. This is an open question; see the analysis file.

**Full analysis with call chain, decompiled source, and empirical log comparison:**
`local-assets/geberit-home-v2.14.1-from-iOS/firmware-update-check-analysis.md` —
section "v1.75.0b1 empirical finding: component 1 alone at RS30.0 is NOT sufficient".

RS30.0 is safe as long as it matches the latest bundled local firmware version.
If Geberit updates the bundled firmware to RS31+, the mock would need updating again.

---

## Fehler on every mock connect — hypothesis (unconfirmed)

**Observation (2026-06-27):** The "Fehler" popup appears on every connect to the mock,
including reconnects with a saved/existing configuration — not only on first-time onboarding.
The real Mera never shows the Fehler after initial setup.

**What the decompiled code confirmed:**
- The app computes a `UniqueId` as CRC32 of the SAP string — the per-device key in local
  storage (confirmed from `ProductIdentifier` source analysis).
- `FirmwareForceUpdateViewModel` has an `_E012` instance field set to `true` after the
  firmware update completion/failure flow. However, `_E012` is an **instance field** — it
  resets to `false` every time the ViewModel is recreated and does NOT persist across
  reconnects on its own.

**Behavioral inference — NOT seen in decompiled code:**
The real Mera never shows the Fehler after initial setup; the mock shows it on every connect
including reconnects with a saved configuration. This suggests the app writes a persistent
"update flow completed" state after proc 0x00 / proc 0x01 finish successfully. The mock
returns zeros for those procedures; the update never completes cleanly; the persistent flag
(if it exists) is never written → flow re-runs on every connect → Fehler. The actual
persistence write was NOT found in the analyzed source.

**What would confirm this:** Implementing proc 0x00 (ctx=0x40) and proc 0x01 (ctx=0x00)
with the correct "update complete" byte values. If the Fehler stops appearing on subsequent
reconnects, the hypothesis is confirmed. The correct response values are unknown without a
real firmware update BLE capture.

**Status: behavioral inference only** — not code-confirmed, not confirmed by testing.
**Superseded 2026-07-17 — see "Investigation update" below.** Proc 0x00 (ctx=0x40) and 0x01
(ctx=0x00) are now implemented (mera_mock.py Phase 9b) with well-formed, cleanly-ACKed
responses. The Fehler/update-flow behavior did not change as this hypothesis predicted — the
actual blocker turned out to be something else entirely (see below), so this hypothesis is not
confirmed and is no longer the leading theory.

---

## Investigation update (2026-07-17) — mock-vs-real BLE capture comparison

First side-by-side capture of the Geberit Home App talking to the mock itself (not just the
real device), via `--btmon-capture` on anneubuntu-studio plus a simultaneous nRF52840 sniff.
Files: `local-assets/Bluetooth-Logs/nRF52840/jens62/firmware-update-mera-comfort/
firmware-update-against-mera-mock/`. Compared against the real device's regular onboarding
(`.../geberit-home-app/onboarding-real-mera.pcapng`) and the real firmware-update capture
(`.../firmware-update-mera-comfort/firmware-update-vom-mac.md`).

**Test setup:** freshly-installed Geberit Home App, mock's firmware profile set to RS28.0 via
the webui, tapped "Update Now" after the expected update-required prompt appeared. Result: no
progress, stuck at 0%, eventually "update failed" after ~9 minutes.

**Ruled out:**
- **Not a framing/ACK bug.** The mock's `0x40/0x00` responses are well-formed and cleanly
  ACKed every single time (confirmed from the mock's own log) — the app is receiving them
  correctly.
- **Not a hidden GATT characteristic.** A `READ_BLOB_REQ` on handle `0x0020` in the real
  capture looked like a lead, but `--gatt-map` resolved it to the standard GAP Device Name
  characteristic (UUID `0x2A00`) — present on the mock too, just at a different handle number
  (`0x0003`, since handle numbers depend on GATT table registration order and aren't
  protocol-meaningful across different server implementations). The sniff confirms the app
  reads it against the mock as well. Not a divergence.
- **`0x40/0x52` (StartFirmwareUpdate) is confirmed never sent** — 0 occurrences across the
  whole ~6-minute session, verified independently via both the mock's own log and the nRF
  sniff. Not a logging blind spot on the mock's side; the write genuinely never happens over
  the air, regardless of tapping "Update Now".

**New lead — the app disconnects from the mock every ~35–70s, reason `0x13` (Remote User
Terminated, i.e. app-initiated, confirmed at the HCI level via btmon):**
- 7 disconnects across the ~6-minute test session.
- After **every single one**, the app re-runs `GetFirmwareVersionList` from scratch —
  effectively restarting the firmware-check flow each time.
- Compared against `onboarding-real-mera.pcapng`: the real device *also* gets
  "Remote User Terminated" disconnects during normal onboarding (twice in 28s — the documented
  two-connection onboarding dance, see `.claude/rules/ble-protocol.md`/historical notes). So
  periodic app-initiated disconnects are normal baseline behavior on their own, not inherently
  a mock bug.
- But compared against `firmware-update-vom-mac.md`: once the real device's update flow
  actually engages, the connection holds rock-steady — **zero** disconnects across the entire
  ~40s pre-update poll and the ~3-minute update itself.

**Current working theory:** not the `0x40/0x00` payload content (the earlier, now-superseded
theory) — the connection to the mock never settles into the single sustained session the real
device holds once genuinely engaged with the update screen. Every ~35–70s the app tears the
connection down and restarts the firmware-check from zero, so tapping "Update Now" never
survives long enough to fire the BLE write. **Not yet answered: why** the mock's connection
gets bounced this often in this state when the real device's doesn't — this is the next thread
to pull, in preference to the payload-content angle.

### New finding (2026-07-17) — the mock itself spontaneously initiates BLE pairing (SMP), the app cooperates, and the mock rejects its own request — possibly the actual cause of the periodic disconnects

**Corrected direction (initial read of a partial `btmon` excerpt got this backwards — see
below):** confirmed via `tshark -Y btsmp` on the existing nRF52840 capture
(`onboarding-and-firmware-update-against-mera-mock-no-success.pcapng`), the full three-message
sequence is:

```
Peripheral (mock) → Central (app):  SMP Security Request  (AuthReq: Bonding, MITM, SecureConnection)
Central (app)     → Peripheral:     SMP Pairing Request    (app cooperating: LTK/IRK/Linkkey both directions)
Peripheral (mock) → Central:        SMP Pairing Failed — Pairing Not Supported
```

**The mock/BlueZ initiates this, unsolicited — the app is just cooperating with what the mock
itself demanded, then getting rejected by the same side that asked.** This is deliberately
unencrypted-at-the-BLE-layer protocol (PIN checks happen at the application layer via proc
`0x44`/`0x64`, never real SMP pairing — see `memory/ble-link-layer-security.md`), so the mock
has no business sending a Security Request at all.

**Frequency and timing — the actual reason this looks important:** happens **7 times** across
one ~6.5-minute capture, at t=34.9s, 145.8s, 216.2s, 263.7s, 311.2s, 347.0s, 394.6s — roughly
every 40–90s. That count and cadence is suspiciously close to the 7 app-initiated
"Remote User Terminated" disconnects found earlier in the same investigation (§ above). Working
hypothesis: the mock spontaneously demands security, the app cooperates, the demand is
immediately rejected by the same side that made it, and the app reasonably gives up on the
connection shortly after — this could be the actual mechanism behind the periodic disconnect
pattern chased earlier via BlueZ-desync/GATT-discovery-stall theories (both of which were ruled
out as insufficient explanations on their own).

**Confirmed NOT caused by an explicit per-characteristic encryption flag**: grepped every
`@characteristic(...)` declaration in `mera_mock.py` — all use plain `READ` /
`WRITE_WITHOUT_RESPONSE` / `NOTIFY`; none use `ENCRYPT_READ`/`ENCRYPT_WRITE`/
`ENCRYPT_AUTHENTICATED_*` (bluez_peripheral does support these flags — checked
`bluez_peripheral/gatt/characteristic.py` on the mock VM, confirming the flag values exist and
aren't being set anywhere in our code). So the Security Request isn't triggered by anything
explicit in the mock's own GATT declarations — it's coming from BlueZ or `bluez_peripheral`
itself, root cause not yet found.

**Caveat — checked and ruled out as a red herring in most *other* captures**: `SMP: Pairing
Failed` events appear in nearly every historical btsnoop capture on the mock VM going back to
June 13 (Alba probes, HACS zeroconf tests, unrelated old Mera runs) — but cross-checking one of
those (`alba-ble20-probe-2.14.1_btmon__2026-06-14_10-01.btsnoop`) shows the failing address
there is `88:66:5A:EF:F7:BC`, a **public** Apple MAC (typical of an accessory like AirPods/
Watch attempting its own unrelated pairing), not tied to any GATT session in that capture. This
specific session's case is confirmed as the actual mock↔app exchange via direct cross-reference
(the app's address, `66:F2:AC:84:E1:6D`, matches the mock's own `BLE client connected:` log
line for that session) — most other occurrences of this event type are unrelated ambient noise.

**Root cause found and fixed (2026-07-17, same day) — but it wasn't the disconnect cause.**
`bluetoothd` on anneubuntu-studio was running as the plain systemd default
(`/usr/libexec/bluetooth/bluetoothd`, no arguments) for this entire investigation — every
`systemctl restart bluetooth` (done repeatedly today for the Trap 16 fixes) silently ran with
BlueZ's built-in **battery plugin** active. That plugin acts as its own GATT *client*, reading
Battery Level from the connected iPad immediately on connect; iOS rejects that read requiring
authentication; BlueZ escalates via the spontaneous Security Request found above; rejected
(`pairable=off`). This exact mechanism was already documented in
`docs/developer/mock-geberit-mera.md` § "Battery plugin interaction" from much earlier work —
the fix (`--noplugin=battery`) had just never been carried into the systemd-managed
`bluetooth.service`, only ever applied when launching a manual/custom `bluetoothd`. Also found:
the *previous* "not necessary" verdict on running a separate custom BlueZ 5.77 `bluetoothd`
was itself correct but incomplete — `bluetoothd --version` reports 5.77 while `dpkg -l bluez`
still claims 5.72, meaning the custom-built 5.77 binary was installed directly over the
system's binary path at some point, making a *second* manually-launched instance redundant —
but `--noplugin=battery` was a separate runtime flag bundled into that same old recommendation,
and got silently dropped along with the "redundant binary" part instead of being carried into
the systemd service definition.

**Fix applied**: systemd drop-in override (`/etc/systemd/system/bluetooth.service.d/
override.conf`) forcing `ExecStart=/usr/libexec/bluetooth/bluetoothd --noplugin=battery`,
confirmed via `ps aux` after `daemon-reload` + `restart bluetooth`.

**Verified via two fresh test sessions post-fix** (`mock-btmon_2026-07-17_14-54.btsnoop` /
`_14-58.btsnoop`): zero SMP events (`Security Request`/`Pairing Request`/`Pairing Failed`) in
either — the mechanism is fully eliminated. **But**: the periodic disconnect pattern is
*unchanged* — still every ~40–90s (64s/42s in one session, 75s/59s in the other) — and
`0x40/0x52` (StartFirmwareUpdate) still never gets sent, 0 occurrences in either session. So
the battery-plugin/SMP-pairing cycle, while a real and now-fixed bug, was **not** the cause of
the periodic disconnects or the core "update never starts" mystery. Both the BlueZ-desync
theory (Trap 16) and this battery-plugin theory are now ruled out as explanations for that
specific pattern — it remains unsolved.

**Update — handle `0x001B` error resolved**: a later, more thorough sweep of a fresh capture
(post battery-plugin-fix) found **zero** `Insufficient Authentication (0x05)` errors anywhere —
every `ATT: Error Response` in that capture is `Attribute Not Found (0x0a)`, which is normal,
expected GATT-discovery probing. So the `0x001B` auth error was entirely the battery plugin's
own doing, not a separate mock-side issue — fully resolved, not just "worth re-checking".

**Also investigated and ruled out**: whether the mock's dead connection-interval-request code
(`_request_short_ci()`, removed 2026-07-17) was contributing — it wasn't; see
`docs/developer/mock-geberit-mera.md` § "Connection-interval request was always dead code" for
the full writeup. Our actual negotiated connection parameters (30ms/0/1000ms) already satisfy
Apple's Bluetooth Accessory Design Guidelines compliance formulas, despite that request always
having silently failed.

**Not yet answered — back to an open question, no remaining hypothesis:**
- Why the app still disconnects every ~40–90s with the battery-plugin cycle eliminated.
- Why `0x40/0x52` never gets sent even during connections with no observed protocol error at
  all (attempts 1/2 in the pre-fix test on 2026-07-17 12:27 were clean, stable, 60-80s
  sessions with zero stalls or pairing attempts, and still never sent it).

### Consolidated summary (2026-07-17) — every "the mock causes this" theory tried so far, and why each was ruled out

Each of these was proposed with genuine supporting evidence at the time, then tested directly
against the actual periodic-disconnect pattern (~35–90s, `Remote User Terminated`) and/or
`0x40/0x52` never being sent — not just plausibility-argued away:

| # | Theory | Evidence that made it plausible | Test performed | Result |
|---|--------|----------------------------------|-----------------|--------|
| 1 | BlueZ adapter desync (Trap 16) | `bluetoothctl show` reported the controller unavailable at one point | Checked `dmesg` for the known kernel signature (`Unexpected advertising set terminated event`) around the actual disconnect timestamps | **Ruled out** — no kernel event correlated; the "unavailable" report was a query-syntax bug (`show hci0` vs. the real MAC), not a real desync |
| 2 | Battery plugin's spontaneous SMP Security Request | Confirmed via pcapng: mock's own BlueZ initiates an unsolicited Security Request, app cooperates, mock rejects its own request — 7 times in one session, suspiciously matching the disconnect count | Fixed via systemd override (`--noplugin=battery`); re-tested with two fresh sessions | **Ruled out** — SMP cycle completely eliminated, but the disconnect pattern and `0x40/0x52` were both unaffected |
| 3 | GATT-discovery stall (CCCD not subscribed within 8s) | Reproduced: some reconnects stall mid-discovery and time out | Compared against the *clean* connections in the same test | **Ruled out** — the clean, stall-free connections also never sent `0x40/0x52` and also eventually disconnected on the same cadence |
| 4 | Dead connection-interval-request code | `_request_short_ci()` silently failed on every connection (confirmed: no such D-Bus method exists on `Device1`) | Checked actual negotiated parameters (30ms/0/1000ms) against Apple's Bluetooth Accessory Design Guidelines compliance formulas | **Ruled out** — parameters already compliant despite the request always having failed; removed as dead code regardless |

**Current status**: no confirmed cause for either the periodic disconnect or the fact that
`0x40/0x52` never gets sent, on the mock side. Every mock-side mechanism found and tested has
been refuted. Next step: determine whether this is even a mock-specific problem at all, via a
longer real-device capture (sniffer started before connecting, then the app left idling
normally on the device's detail screen for 5+ minutes) — if the real device shows the same
disconnect pattern under equivalent idle conditions, this reframes as normal Geberit Home App
behavior rather than a mock defect.

### Resolution (2026-07-17, same day) — the periodic disconnect was a symptom, not an independent bug

The real-device comparison above became unnecessary. Testing the mock with `_FW_COMPONENT_VERSIONS_RS28`
changed to report `RS28.0 TS199` **uniformly across every component** (not just 1+11 — see the
consolidated summary below) produced: the dismissible "Fehler" (confirmed working), successful
navigation through other app menus, **and a single BLE connection lasting 9 minutes 27 seconds
with zero disconnects** (`uniform-rs28-fehler-dismissed-firmware-current-blank-version_2026-07-17_20-04.log`,
connect 20:07:33 → last activity 20:17:00, no `BLE client disconnected` line anywhere in the file).
Every single prior test that day, without exception, showed a disconnect every ~35–90s.

**Conclusion: the periodic disconnect was downstream of the app being stuck in the blocking
force-update screen, not an independent BLE/mock-level bug.** Once the app isn't stuck there,
the connection is completely stable. This resolves the disconnect thread from the consolidated
summary below — it wasn't a fifth theory to test, it was a side effect of the same root
condition (heterogeneous per-component values triggering the blocking screen) all along.

**Remaining, smaller, distinct problem**: with uniform RS28.0 applied, the app's Maintenance →
Firmware Update screen states the firmware **is current** and shows a blank `"--"` for the
version — even though every component is reporting a version below the RS30.0 target. Checked
and ruled out: `GetSOCApplicationVersions` (proc `0x81`) is not the cause — confirmed via the
real capture that it stays constant (`31 30 12 00`, "SOC 10.18") across the genuine RS28→RS30
update on the real device too, and the mock's hardcoded value already matches it. Root cause of
the blank-version/"current" display not yet found — needs tracing what the app actually
requests specifically when navigating to that menu (not just the onboarding-time check), which
is now finally traceable without disconnects fighting for attention at the same time.

**Confirmed clean (2026-07-17, same session) — not confounded by device identity.** The first
successful test above reconnected to a device originally onboarded with the old
`tools/mock-geberit-mera.py` v1.63.0b1 script, raising the possibility the app-side
`CRC32(SAP)`-keyed persistence hypothesis (see the superseded "Fehler on every mock connect"
section above) was doing some of the work rather than the uniform-RS28.0 profile itself. Redone
as a fully clean test: fresh onboarding, current mock only (v1.88.0b1), no reused device
identity. **Identical result** — dismissible Fehler (confirmed OK), all other menus and settings
changes working normally, Maintenance → Firmware Update still showing "current" / blank `"--"`.
Rules out the device-identity confound; the uniform-RS28.0 mechanism reproduces independently.

**UI design clarification (same session)**: the app's Firmware Update screen shows exactly **one**
overall version string, not a per-component breakdown, on both the mock and the real device — by
design ("more would only confuse the user," per direct observation). Still open: whether that one
string ever renders as blank `"--"` on a real device under any circumstance, or whether blank is
mock-specific — not yet confirmed either way.

**Re-flagged (2026-07-17), first noted June 2026 — inconsistent firmware-version reporting
correlates with the app finding two "unconfigured" devices instead of one.** User-reported
recurring observation, re-raised during the incremental single-component test (component 1
changed, all others left at the real baseline — i.e. deliberately inconsistent right now).

There is a **prior, different explanation already on record** for a superficially similar
symptom, from the large prompting-log transcript (`local-assets/claude-prompting-et-al_mera-mock.txt`,
June): *"Two unconfigured devices before bluetoothd restart, one after — that means iOS was
seeing two distinct BLE advertisers. The most likely cause: a previous mock run left a stale BLE
advertisement registered in BlueZ."* That explanation is about **stale advertisements** left over
from a prior mock run — entirely independent of what firmware-version *data* is being reported.
**Not yet confirmed whether today's re-occurrence is the same stale-advertisement mechanism
recurring coincidentally alongside firmware-version testing, or a genuinely new, data-driven
mechanism where inconsistent per-component version data itself causes the app to perceive two
devices** (e.g. if the app fingerprints/deduplicates discovered devices partly using reported
firmware-version data, an inconsistent read within one discovery session could plausibly produce
two different fingerprints for the same physical device). Needs a clean, controlled check:
whether "two unconfigured devices" appears specifically correlated with inconsistent version
data across repeated tests, independent of any stale-advertisement conditions (fresh mock
restart, no prior leftover state) — not yet done.

---

## Open questions

1. **What are `arg1` and `arg2` in `GetFirmwareVersionList`?**
   - All simple payloads (empty, 1 byte, 4 bytes) return empty data
   - 2-byte payload `0000` crashes — probably triggers a device response we can't parse
   - **Only BLE sniffing can answer this**

2. **What fields does `GetSOCApplicationVersions` (0x81) response contain?**
   - Known: 4-byte response `31301200`
   - Unknown: field layout, whether it maps to human-readable version strings

3. **Where does "RS28.0 TS199" come from in the official app?**
   - Not from procedure 0x81 (wrong byte count)
   - Not from procedure 0x0E with any tested payload
   - **Requires BLE sniffing to identify source procedure**

---

## Related files

| File | Role |
|------|------|
| `aquaclean_core/Api/CallClasses/GetSOCApplicationVersions.py` | CallClass (procedure 0x81) |
| `aquaclean_core/Api/CallClasses/GetFirmwareVersionList.py` | CallClass (procedure 0x0E, configurable payload) |
| `aquaclean_core/Api/CallClasses/Dtos/SOCApplicationVersion.py` | Response DTO (stub) |
| `aquaclean_core/Api/CallClasses/Dtos/FirmwareVersionList.py` | Response DTO for 0x0E (stub, unused) |
| `aquaclean_core/Clients/AquaCleanBaseClient.py` | `get_soc_application_versions_async()`, `get_firmware_version_list_async(payload)` |
| `aquaclean_core/Clients/AquaCleanClient.py` | `soc_application_versions` + `firmware_version_list` cache attrs + events |
| `local-assets/tmp.txt` | Unimplemented C# procedures reference |
