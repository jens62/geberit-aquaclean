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

## iOS app — firmware update check mechanism (from decompiled source)

Investigated 2026-06-26 from `local-assets/geberit-home-v2.14.1-from-iOS/decompiled/`.

### How the app decides to show the "update firmware" prompt

The app uses a **cloud-based firmware service** (`FirmwareServiceClient`) — not local version
comparison. The check calls Geberit's cloud API to fetch a list of `FirmwareDto` objects,
each with per-node firmware files and a `RuleSet`. The rule set is then evaluated against the
LIVE DEVICE (DataPoint reads over BLE).

Flow:
1. `FirmwareServiceClient.RefreshCacheAsync()` — fetches `FirmwareDto[]` from cloud, keyed by `ContentId`. Cached in `FirmwareServiceCacheData.json` on the device. If the cache is fresh (`ContentId` unchanged) the network call is skipped.
2. `IsUpdateAvailableAsync(product)` → `e.e()` filters cloud entries by `product.DeviceSeries == 248` AND `product.DeviceVariant` (derived from article number via `AcDeviceTypeHelper`).
3. For each matching cloud `FirmwareDto` with `PackageVersion > current`, checks `nodeFirmware.RuleSet` against the device via `FirmwareRuleVerification.CheckFirmwareRuleSet`.
4. If any RuleSet passes → `FirmwareForceUpdateViewModel` is shown (blocking screen, blocks onboarding completion).

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

1. **First character check** (`AcDeviceTypeHelper.b()`):
   - starts with `'G'` → `AcMeraClassic`
   - starts with `'H'` (or anything else) → `AcMeraComfort`
   (for article prefix `146.21` only)

2. **CRC32 → `UniqueId`** (`AquaCleanProduct.c()`):
   ```csharp
   uint value = new Crc32(Crc32Algorithm.Standard).Calculate(Encoding.ASCII.GetBytes(serialNumber));
   return new ProductIdentifier(series, variant, 0u, value);
   ```
   The full SAP string is CRC32'd (ASCII) to produce the `UniqueId` field.
   The resulting `ProductIdentifier` format is `{Series:X2}{Variant:X2}-0000000[{CRC32:X8}]`.
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
2. **All nodes use `deployContract=AquaCleanV1`** — the `global::e` filter requires
   `Ble2V1 || EspV1 || GatewayV1`. Since every Mera node is `AquaCleanV1`, none match.
   See `global::e` findings below for the definitive consequence.

### `FirmwareServiceLocal.cs` — findings (2026-06-26)

Read from `local-assets/geberit-home-v2.14.1-from-iOS/decompiled/Geberit.ComLib.Firmware/`.

`FirmwareServiceLocal` is a **thin shell**. It implements `IFirmwareService` but
delegates ALL update logic to `global::e` — the same obfuscated class used by
`FirmwareServiceClient`. The two services differ only in their data source:

| Service | `FirmwareVersions` source |
|---------|--------------------------|
| `FirmwareServiceClient` | Cloud API (`prod.firmwarev1.services.geberit.com`) |
| `FirmwareServiceLocal` | Local `.zip` files scanned from `firmwareFolderPath/*.zip` |

Method delegation:

| Method | Delegates to |
|--------|-------------|
| `IsUpdateAvailableAsync` | `global::e.b(this, product, token, onlyOfflineUpdates)` |
| `GetActiveUpdateAsync` | `global::e.c(this, product, token, onlyOfflineUpdates)` |
| `GetAvailableUpdatePackagesAsync` | `global::e.e(this, product, token, upgradesOnly)` |
| `GetRecoveryUpdate` | `global::e.d(this, series, variant, bootVariant, onlyOfflineUpdates)` |
| `CheckFirmwareRuleSetAsync` | `FirmwareRuleVerification.CheckFirmwareRuleSet` (same as cloud) |

Local zip loading: constructor scans `firmwareFolderPath/*.zip`, reads `FirmwarePackage.json`
from each zip, creates `FirmwareDto` with `IsActive = true` and `Channel = Release`.
`IsOfflineAvailable` always returns `true`.

### `global::e` (`e.cs`) — findings (2026-06-26)

Read from `local-assets/geberit-home-v2.14.1-from-iOS/decompiled/Geberit.ComLib.Firmware/e.cs`.

This is the shared implementation class for all `IFirmwareService` update decisions.
All five methods are static; `FirmwareServiceClient` and `FirmwareServiceLocal` both
delegate to it — the update logic is completely identical for cloud and local paths.

#### `a()` — `GetProductVersion(service, product)`

Builds the device's current `ProductVersion` by matching the device's RS/TS firmware
against the cloud/local firmware list:

1. Builds `NodeVersion` list: `("0x00", wirelessFirmwareVersion)` if present + `("0x01", ConvertVersion(RsVersion, TsVersion))`
2. Filters `FirmwareVersions` to packages matching `DeviceSeries` + `DeviceVariant`
3. Iterates descending by `PackageVersion`; for each, checks if any `NodeFirmware` satisfies:
   - `NodeId == "0x01"` AND `RsTsVersion == device's current b2` AND `DeployContract == Ble2V1 || EspV1 || GatewayV1`
4. First match → return `ProductVersion(packageVersion, nodeVersions)`
5. No match → return `ProductVersion(PackageVersion=null, nodeVersions)`

**For Mera (all `AquaCleanV1`):** condition 3 never matches → always returns `PackageVersion = null`.

This means the app cannot identify which cloud package corresponds to the Mera's current
firmware. From the app's perspective, the Mera's firmware version is always **unknown**.

#### `e()` — `GetAvailableUpdatePackagesAsync(service, product, token, upgradesOnly)`

1. Calls `a()` → `b2.PackageVersion` is `null` for Mera
2. Filters `FirmwareVersions`: `Series == DeviceSeries && Variants.Contains(DeviceVariant) && DownloadUrl != null && (b2.PackageVersion == null || !upgradesOnly || item.PackageVersion > b2.PackageVersion)`
   - For Mera: `b2.PackageVersion == null` → always true → ALL packages of the right Series/Variant pass
3. Iterates NodeFirmwares looking for: `(Ble2V1 || EspV1 || GatewayV1) && NodeId == "0x01" && RuleSet passes`
   - For Mera: `AquaCleanV1` never matches → **returns empty list**

#### `b()` — `IsUpdateAvailableAsync`

```
return (await e(..., upgradesOnly: true)).Any(p => p.IsActive && ...)
```

`e()` returns empty list for Mera → `.Any()` → **always returns `false`**.

**`IsUpdateAvailableAsync` is definitively always `false` for all Mera (AquaCleanV1) devices,
regardless of firmware version. The firmware update prompt cannot be triggered via this path.**

#### `c()` — `GetActiveUpdateAsync`

Calls `e()`, filters active + offline-available, returns highest-priority channel via `f()`.
Empty list for Mera → returns `null`.

#### `d()` — `GetRecoveryUpdate`

Filters by `Series + BootloaderVariant/Variant`, iterates NodeFirmwares for
`(Ble2V1 || GatewayV1) && NodeId == "0x01" && FilePath != null`. No EspV1 here.
Still `AquaCleanV1` never matches → returns `null` for Mera.

#### `f()` — channel priority selector

Returns first descriptor by channel priority: Dev > Test > ReleaseCandidate > Release.
Used by `c()` and `d()` to pick the highest-priority available update.

### `ConnectToAquaCleanViewModel.cs` — instantiation site (2026-06-26)

Read from `local-assets/geberit-home-v2.14.1-from-iOS/decompiled/Home.Core/Home.Core.ViewModels.Devices.AquaClean/ConnectToAquaCleanViewModel.cs`.

`FirmwareForceUpdateViewModel` is instantiated at line 1192 inside the `_E006` async state
machine (the post-connect flow):

```csharp
if (m__E002 || m__E001)  // m__E001 is readonly bool, never assigned → always false
{
    NavigationService.Navigate<FirmwareForceUpdateViewModel, IFirmwareUpdateExecutor>(
        (ConnectedDevice as IBaseAquaCleanDevice).AquaCleanProduct);
}
```

Effective condition: **`m__E002`** (= `IsForceUpdateNecessary`).

`m__E002` is set immediately after the device connects:
```csharp
bool result = await _E004(connectedDevice);
m__E002 = result & featureFlag;
```

#### `_E004(IBaseAquaCleanDevice device)` — the actual firmware check

This is the AquaClean-specific firmware check, entirely separate from `IFirmwareService`:

```csharp
IAquaCleanProduct product = device.AquaCleanProduct;

// Early exits — no update
if (product is NullAquaCleanProduct) return false;

Version version = product.GetVersion();  // parsed from proc 0x0E RS version
if (version == null || string.IsNullOrEmpty(version.ToString())) return true;  // force update

// Fetch cloud-synced remote settings (honours cache: only syncs if stale)
await RemoteSettingsService.SyncRemoteSettingsIfNecessary<AppRemoteSettings>(GeberitDeviceType.App);
AppRemoteSettings settings = await RemoteSettingsService.GetRemoteSettings<AppRemoteSettings>(GeberitDeviceType.App);

Version minVersion = settings.GetDeviceApiMinVersion(device.ConfigurationInfo.DeviceType);
if (minVersion == null) throw new Exception(...);  // caught in caller → m__E002 stays false

return minVersion.Major > version.Major;  // AND'd with featureFlag in caller
```

#### `AppRemoteSettings.device_api_min_version`

`AppRemoteSettings` is fetched from Geberit's cloud (a remote config service, separate from
the firmware cloud). The relevant field:

```json
{ "device_api_min_version": { "<deviceType>": "30.0" } }
```

`GetDeviceApiMinVersion(deviceType)` looks up the dictionary by `deviceType` string key and
parses the value as a `System.Version`. Returns `null` if key absent or dictionary empty.

**If the key is absent** → `null` → exception thrown → caught in caller → `m__E002 = false`
→ **no force update prompt**.

**If the key is present with `"30.x"`** → `Major = 30` → `30 > 28 (RS28)` → `true`
→ `m__E002 = true` → **force update prompt shown**.

### Why mock RS28.0 triggers the prompt but real device does not — ROOT CAUSE FOUND

The trigger is **`AppRemoteSettings.device_api_min_version`**, synced from Geberit's remote
config cloud (separate from the firmware API). `SyncRemoteSettingsIfNecessary` honours an
internal TTL cache — it only hits the network when the cached value is stale.

| | Mock (RS28.0) | Real device (RS28.0) |
|--|--|--|
| `RemoteSettingsService` cache | Fresh / recently expired → network fetch → current Geberit settings → `device_api_min_version["AquaClean"] = "30.0"` present | Stale / from before Geberit added the key → key absent → `GetDeviceApiMinVersion` returns `null` → exception → `m__E002 = false` |
| Force update fires? | **Yes** — `30 > 28` | **No** — `null` key |

Deleting the device from the app and rebooting the iPad does **not** clear the
`RemoteSettingsService` cache — it lives in the app's persistent storage independently of
the device list. Only a cache expiry or app reinstall would force a fresh fetch.

**Ruled-out hypotheses (complete list):**

| Hypothesis | Ruled out because |
|------------|------------------|
| iOS Bluetooth pairing state | Mera uses zero SMP — no iOS-level BLE pairing |
| `FirmwareServiceCacheData.json` per-device state | Global cache keyed by `ContentId` only |
| DataPoint RuleSet predicates | All 15 Mera NodeFirmwares have empty RuleSets (HAR) |
| `FirmwareServiceLocal` separate AquaCleanV1 path | Delegates to same `global::e` |
| `IFirmwareService.IsUpdateAvailableAsync` | Always `false` for AquaCleanV1 — confirmed in `e.cs` |

### Practical fix for the mock

**Keep `_FW_COMPONENT_VERSIONS[1]` at RS30.0 TS206** (applied in v1.72.0b1).

With RS30.0: `30 > 30 = false` → no force update, regardless of what the remote settings say.
RS30.0 is always safe as long as it matches or exceeds the `device_api_min_version` major.
If Geberit ever bumps the minimum to RS31, the mock would need updating again.

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
