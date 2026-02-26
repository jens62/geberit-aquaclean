# Firmware Version — Implementation Status & Roadmap

## Overview

Two separate procedures exist for reading firmware/version data from the AquaClean device:

| Procedure | Name | Status |
|-----------|------|--------|
| `0x81` | `GetSOCApplicationVersions` | CallClass + full wiring exists; response received but **not parsed** (raw hex) |
| `0x0E` | `GetFirmwareVersionList` | DTO stub exists; **no CallClass**, never been called |

The core blocker for both is the same: the **binary response format is unknown** and was never ported from the C# reference repo.

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

**The response IS received from the device.** The only missing piece is parsing it:

```python
# GetSOCApplicationVersions.py — result() method
def result(self, data):
    logger.info("Not yet fully implemented.")
    readable_data = ''.join(f'{b:02X}' for b in data)
    # Deserializer call is commented out:
    # ds = Deserializer.Deserializer()
    # di = ds.deserialize(SOCApplicationVersion.SOCApplicationVersion, data)
    return readable_data  # Returns raw hex string, e.g. "0102030405..."
```

The DTO stub (`Dtos/SOCApplicationVersion.py`) only has placeholder fields `A: bytes`, `B: bytes` — no actual field mapping.

### MQTT in persistent mode: empty handler

In `ServiceMode`, the `SOCApplicationVersions` event handler is an empty stub:

```python
# main.py ~line 1256
async def soc_application_versions(self, sender, args):
    pass  # No MQTT publish in persistent mode
```

On-demand mode publishes correctly (via the REST → MQTT bridge in `get_soc_versions()`).

---

## What does NOT exist yet

### `GetFirmwareVersionList` (0x0E)

- **DTO exists** at `aquaclean_core/Api/CallClasses/Dtos/FirmwareVersionList.py` — stub only:
  ```python
  @dataclass
  class FirmwareVersionList:
      def __init__(self, A: int = 0, B: bytes = None):
          self.A = A
          self.B = B if B is not None else [None] * 60
  ```
- **No CallClass** — `GetFirmwareVersionList.py` does not exist.
- This procedure was listed in `local-assets/tmp.txt` (C# reference):
  ```
  [Api(Context = 1, Procedure = 0x0E)]
  FirmwareVersionList GetFirmwareVersionList(object arg1, object arg2);
  ```
  Note: it takes **two arguments** — their type/meaning is unknown.

---

## The blocker: binary response format unknown

The C# reference repo (thomas-bingel) had a `Deserializer` class that mapped byte offsets to struct fields, but it was never ported. The firmware strings are likely ASCII or BCD-encoded inside the response bytes, but the exact layout requires one of:

1. **Analyse the hex output** from a live device (easiest — see below)
2. **BLE-sniff the official Geberit Home app** while it displays firmware info, then correlate bytes

---

## How to get the raw hex today

Run on the Raspberry Pi against a live device:

```bash
aquaclean-bridge --mode cli --command soc-versions 2>/dev/null
```

Example output (format, not real data):
```json
{
  "status": "success",
  "command": "soc-versions",
  "data": {
    "soc_versions": "0102030405060708090A0B0C..."
  }
}
```

Post the hex string — it likely contains ASCII firmware version strings with binary length-prefix framing, which can be decoded by inspection.

---

## Implementation steps (once format is known)

### Step 1 — Implement `GetSOCApplicationVersions.result()` deserialization

Update `aquaclean_core/Api/CallClasses/GetSOCApplicationVersions.py`:

```python
def result(self, data):
    # TODO: parse data bytes into firmware version fields
    # Likely: length-prefixed strings or fixed-size struct
    return parsed_result
```

Update the DTO `Dtos/SOCApplicationVersion.py` with real fields (e.g. `application_version`, `bootloader_version`, etc. — TBD from byte analysis).

### Step 2 — Create `GetFirmwareVersionList.py` CallClass

Pattern is identical to other CallClasses:

```python
class GetFirmwareVersionList:
    def __init__(self):
        self.api_call_attribute = ApiCallAttribute(0x01, 0x0E, 0x01)

    def get_api_call_attribute(self):
        return self.api_call_attribute

    def get_payload(self):
        return bytearray()  # arg1, arg2 types unknown — may need non-empty payload

    def result(self, data):
        # TODO: parse FirmwareVersionList
        return ''.join(f'{b:02X}' for b in data)
```

Note: the C# signature shows two arguments (`arg1`, `arg2`). Their types are unknown — may require a non-empty payload. Start with empty and see if the device responds.

### Step 3 — Add BaseClient method

In `AquaCleanBaseClient.py`, add:

```python
async def get_firmware_version_list_async(self):
    api_call = GetFirmwareVersionList()
    # ... same pattern as get_soc_application_versions_async()
```

### Step 4 — Wire into `system-info` via cache

`system-info` is intentionally BLE-free (useful for diagnosing installs). **Do not require BLE for system-info.** Instead:

- Cache parsed firmware version in `device_state` after first successful poll/connect (same pattern as `sap_number`, `serial_number`)
- `system-info` reads from `device_state` cache → returns `null` if not yet polled, populated after first connect

```python
# In system-info data assembly (main.py get_system_info())
"firmware": {
    "soc_versions": device_state.get("soc_versions"),      # null until first connect
    "firmware_list": device_state.get("firmware_version_list"),  # null until first connect
}
```

### Step 5 — Full interface update (mandatory)

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

## Open questions

1. **What are `arg1` and `arg2` in `GetFirmwareVersionList`?** — Try empty payload first; if device returns an error or no response, BLE sniffing is needed.
2. **What fields does the `SOCApplicationVersions` response contain?** — Application version? Bootloader? Hardware revision? Resolved by hex analysis.
3. **Are SOC versions and firmware version list the same data or different?** — May be redundant; `GetFirmwareVersionList` may be a superset.

---

## Related files

| File | Role |
|------|------|
| `aquaclean_core/Api/CallClasses/GetSOCApplicationVersions.py` | CallClass (procedure 0x81) |
| `aquaclean_core/Api/CallClasses/Dtos/SOCApplicationVersion.py` | Response DTO (stub) |
| `aquaclean_core/Api/CallClasses/Dtos/FirmwareVersionList.py` | Response DTO for 0x0E (stub, unused) |
| `aquaclean_core/Clients/AquaCleanBaseClient.py` | `get_soc_application_versions_async()` |
| `aquaclean_core/Clients/AquaCleanClient.py` | `soc_application_versions` cache attr + event |
| `local-assets/tmp.txt` | Unimplemented C# procedures reference |
