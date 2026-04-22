# Zeroconf / Auto-Config Setup Flow — Design

**Status:** design agreed, implementation pending.

This document describes the planned automatic configuration flow for both the standalone bridge and the HACS integration. The flow reuses the logic already implemented in `tools/aquaclean-connection-test.py` — no protocol code is duplicated.

---

## Architecture

The core discovery and probe logic is shared between standalone and HACS. Only the presentation layer differs:

| Context | UI layer | Config storage |
|---|---|---|
| Standalone bridge | Interactive CLI (`aquaclean-bridge --setup`) | `config.ini` |
| HACS integration | HA config flow wizard | `config_entry.data` |

---

## Step 1 — ESPHome Discovery (mDNS)

Scan the local network for ESPHome proxies advertising `_esphomelib._tcp.local`.

| Result | Options presented to user |
|---|---|
| None found | Enter host manually OR use local BLE adapter |
| One found | Use the discovered proxy / enter manually / use local BLE |
| Multiple found | Select from list / enter manually / use local BLE |

An optional `[ESPHOME] name_filter` key (e.g. `aquaclean`) narrows the selection when multiple ESPHome proxies are present on the network.

Existing implementation: Step 0 of `tools/aquaclean-connection-test.py`.

---

## Step 2 — ESPHome API Connect

Test the API connection to the selected or manually entered host.

On failure: show the error and hint (E1001/E1002 equivalent) and return to Step 1.

Existing implementation: Steps 1–3 of `tools/aquaclean-connection-test.py`.

---

## Step 3 — BLE Scan + Device Identification

Scan for BLE advertisements and check each discovered device against all known Geberit UUID profile sets (see UUID Profiles section below).

| Result | Action |
|---|---|
| No Geberit device found | Inform user; ask for manual MAC address |
| One Geberit device found | Proceed with it |
| Multiple Geberit devices found | List them; ask user to select |

After identifying the device, run the full protocol probe (GetDeviceIdentification) to confirm connectivity and retrieve SAP number, serial number, and firmware version.

Existing implementation: Steps 4–6 of `tools/aquaclean-connection-test.py` with `--dynamic-uuids`.

---

## Step 4 — Unknown Device (non-standard UUID profile)

If no known UUID profile matches the discovered device, prompt for the MAC address and run `aquaclean-connection-test.py --dynamic-uuids` to auto-detect UUIDs and probe the device.

**On success (3.1.0.1):**
1. Store the discovered UUID set in the config (see Storage section).
2. Print the reporting template (see Reporting section) and ask the user to submit a GitHub issue.

**On failure (3.1.0.2):**
1. Show a comprehensive explanation of what was tried and what failed.
2. Print the reporting template and ask the user to submit a GitHub issue.

A UUID set is only marked "confirmed" once GetDeviceIdentification succeeds over it.

---

## Step 5 — Manual Entry Fallback

Every auto-detect step must have a manual escape hatch:
- ESPHome host / port
- BLE MAC address
- Local BLE (no ESPHome proxy)

---

## UUID Profile Registry

Known UUID profiles are stored as named sets in the package (e.g. `known_uuid_profiles.json`). The package version is intentionally overwritten on update — updates may add newly confirmed profiles from community reports.

User-discovered custom profiles (from Step 4 success) are stored in `config.ini` / `config_entry.data`, not in the package, so they survive updates independently.

### Currently known profiles

| Name | Service UUID | Write char | Notify char | Status |
|---|---|---|---|---|
| Standard | `3334429d-90f3-4c41-a02d-5cb3a03e0000` | `...a13e0000` | `...a53e0000` | Confirmed |
| Variant A | `0000fd48-0000-1000-8000-00805f9b34fb` | `559eb001-2390-11e8-b467-0ed5f89f718b` | `559eb002-2390-11e8-b467-0ed5f89f718b` | Probe pending |

---

## Config Storage

### Standalone (`config.ini`)

`config.ini` is not part of the pip package and survives `pip install --upgrade`.

```ini
[ESPHOME]
host = aquaclean-proxy.local
port = 6053

[BLE]
device_id = 38:AB:00:00:00:01
uuid_profile = standard        ; or "variant_a" or "custom"

; populated automatically for custom profiles:
; uuid_service  = ...
; uuid_write_0  = ...
; uuid_read_0   = ...
```

### HACS (`config_entry.data`)

Stored in HA's `.storage/core.config_entries` — survives integration updates.

```python
config_entry.data = {
    "mac":           "38:AB:00:00:00:01",
    "esphome_host":  "aquaclean-proxy.local",
    "esphome_port":  6053,
    "uuid_service":  "3334429d-90f3-4c41-a02d-5cb3a03e0000",
    "uuid_write_0":  "...",
    "uuid_read_0":   "...",
}
```

---

## Reporting New Models to the Project

When a probe succeeds with a non-standard UUID profile, or when a probe fails on a completely unknown device, print the following template and ask the user to open a GitHub issue. The connection test already has all the data; this is one print block with no network calls or credentials required.

```
=== New Geberit model detected — please report ===
Open a new issue at https://github.com/jens62/geberit-aquaclean/issues/new
and paste the following:

Geberit model (e.g. AquaClean Mera Comfort, Sela, 8000plus): ← please fill in
Device MAC:    E4:85:01:CD:B0:08
SAP number:    <from GetDeviceIdentification>
Serial number: <from GetDeviceIdentification>
Firmware:      <from GetDeviceIdentification>

GATT profile:
  <full GATT table dump>

UUID set used:
  Service:  <injected service UUID>
  WRITE_0:  <injected write UUID>
  READ_0:   <injected notify UUID>
```

The Geberit model name is explicitly requested because the SAP number and serial number alone do not identify the product line (Mera Comfort, Sela, 8000plus, etc.).

---

## Implementation Notes

When implementing:
1. Extract shared discovery logic into a module (e.g. `aquaclean_console_app/setup/discovery.py`) — do not duplicate code from `aquaclean-connection-test.py`.
2. HACS: wire into config flow per the step mapping in CLAUDE.md ("HACS config flow integration" section).
3. Standalone: `aquaclean-bridge --setup` interactive CLI writing `config.ini`.
4. The reporting template print block belongs at the end of any `--dynamic-uuids` success or failure path involving a non-standard profile.
