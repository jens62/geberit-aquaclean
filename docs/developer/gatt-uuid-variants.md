# GATT UUID Variants — Non-Standard Geberit BLE Profiles

**Analysis date:** 2026-04-21
**Status:** Variant A based on one device report (E4:85:01:CD:B0:08). GATT discovery confirmed. Write type fix applied (commit 7433b6b) — protocol probe outcome pending user confirmation.

---

## Overview

Most Geberit AquaClean devices use a single vendor service UUID (`3334429d-...`) for all GATT communication. At least one device variant uses a completely different GATT layout: a BT SIG member-registered service UUID (`0000fd48-...`) as the data channel, with Geberit vendor characteristics inside it.

---

## Known GATT Profiles

### Standard Profile (confirmed working)

Observed on: HB2304EU298413 and most user-reported devices.

| Role | UUID |
|------|------|
| Service | `3334429d-90f3-4c41-a02d-5cb3a03e0000` |
| WRITE_0 (command write) | `3334a2d3-90f3-4c41-a02d-5cb3a03e0000` |
| WRITE_1 (secondary write) | `3334a4d4-90f3-4c41-a02d-5cb3a03e0000` |
| READ_0 / NOTIFY_0 | `3334a5d5-90f3-4c41-a02d-5cb3a03e0000` |
| NOTIFY_1 (overflow A6) | `3334a6d6-90f3-4c41-a02d-5cb3a03e0000` |
| NOTIFY_2 (overflow A7) | `3334a7d7-90f3-4c41-a02d-5cb3a03e0000` |
| NOTIFY_3 (overflow A8) | `3334a8d8-90f3-4c41-a02d-5cb3a03e0000` |

The bridge targets this profile by default. See `BluetoothLeConnector` class-level UUID constants.

### Variant A (identified 2026-04-21, protocol probe pending)

Observed on: `E4:85:01:CD:B0:08` (single report, model and firmware unknown).

Full GATT table:

| Handle | Service / Characteristic | UUID | Properties |
|--------|--------------------------|------|------------|
| 0x0001 | Generic Attribute (standard) | `00001801-0000-1000-8000-00805f9b34fb` | — |
| 0x0005 | Generic Access (standard) | `00001800-0000-1000-8000-00805f9b34fb` | — |
| 0x000C | Geberit vendor service A | `559eb100-2390-11e8-b467-0ed5f89f718b` | — |
| — | Char (write) | `559eb101-2390-11e8-b467-0ed5f89f718b` | WRITE_NO_RESP |
| — | Char (read) | `559eb110-2390-11e8-b467-0ed5f89f718b` | READ |
| 0x001C | **BT SIG member service (data channel)** | `0000fd48-0000-1000-8000-00805f9b34fb` | — |
| — | **Write characteristic** | `559eb001-2390-11e8-b467-0ed5f89f718b` | WRITE_NO_RESP |
| — | **Notify characteristic** | `559eb002-2390-11e8-b467-0ed5f89f718b` | NOTIFY |
| 0x002C | Device Information (standard) | `0000180a-0000-1000-8000-00805f9b34fb` | — |

**Probable role mapping:**

| Standard profile role | Variant A equivalent |
|-----------------------|----------------------|
| Service | `0000fd48-...` |
| WRITE_0 | `559eb001-2390-11e8-b467-0ed5f89f718b` |
| NOTIFY_0 | `559eb002-2390-11e8-b467-0ed5f89f718b` |
| WRITE_1 | Unknown — possibly `559eb101-...` (unconfirmed) |

**What is NOT yet known for Variant A:**
- Whether the Geberit framing protocol (frame format, procedure codes) is identical over these UUIDs (protocol probe fix applied 2026-04-21, outcome pending)
- What the `559eb100` service (with `559eb101`/`559eb110`) is for — firmware update channel? configuration register?
- Which specific Geberit models or firmware versions use this profile
- Whether `559eb101` maps to WRITE_1 in the bridge's dual-write scheme (standard profile has WRITE_0 + WRITE_1; Variant A appears to have only one write characteristic in the data service)

**Confirmed for Variant A (2026-04-21):**
- `559eb001` is `[WRITE_NO_RESP]` only (GATT properties 0x04, no 0x08). Using ATT_WRITE_REQUEST caused GATT error 0x03 "Write not permitted".
- Fix: `ESPHomeAPIClient.write_gatt_char` auto-detects write type from GATT properties at connect time (commit 7433b6b). No configuration needed.

### ATT error 0x03 — diagnostic meaning

`error=3 description=Write not permitted` = **ATT_ERR_WRITE_NOT_PERMITTED**: the characteristic does not support the WRITE_REQUEST operation. This is a write-type mismatch, not a security/pairing issue. Security errors are 0x05 (insufficient authentication) and 0x08 (insufficient encryption). Error 0x03 on a `[WRITE_NO_RESP]`-only characteristic unambiguously means: use `response=False` (ATT_WRITE_COMMAND).

### Protocol probe is not read-only

`--dynamic-uuids` protocol probe runs `connect_async()` + `subscribe_notifications_async()` + `get_device_identification_async()`. All three involve writes to the write characteristic:
1. `connect_async()` sends SubscribeNotifications (4× Proc 0x01/0x13) to the write char
2. `get_device_identification_async()` sends the GetDeviceIdentification request to the write char
3. Response arrives via NOTIFY on the notify char

The `[FAIL] GATT profile` result for Variant A is **expected and correct** — `3334429d` service is absent. `--dynamic-uuids` handles it via UUID injection. It does not need to be fixed.

### Next unknown after write fix is confirmed

If the protocol probe passes (write fix works), the next question is whether the Geberit framing protocol (frame format, procedure codes, response structure) is identical over `559eb001`/`559eb002`. iPhone BLE traffic from the Geberit Home App connected to `E4:85:01:CD:B0:08` would confirm or refute this.

---

## The `559eb` Vendor UUID Namespace

All Geberit-specific UUIDs in Variant A share the prefix `559eb` with the suffix `-2390-11e8-b467-0ed5f89f718b`. This is Geberit's registered Bluetooth vendor UUID namespace, distinct from the `3334xxxx-90f3-4c41-a02d-5cb3a03e0000` namespace used in the standard profile. The presence of either namespace in a GATT table confirms the device is a genuine Geberit product.

The `0000fd48-0000-1000-8000-00805f9b34fb` service UUID uses the Bluetooth SIG base UUID format with a 16-bit member-assigned company UUID (FD48). The Geberit vendor characteristics inside it (`559eb001`, `559eb002`) still use Geberit's own namespace, making this service immediately recognizable as Geberit-owned despite the SIG-format outer UUID.

---

## How the Bridge Handles UUID Variants

`BluetoothLeConnector` defines all UUIDs as class-level constants. The `--dynamic-uuids` flag on `tools/aquaclean-connection-test.py` triggers automatic GATT discovery and, if a non-standard Geberit service is found, overrides these constants via instance-attribute shadowing before running the protocol probe:

```python
connector.SERVICE_UUID                = UUID(discovered_svc_uuid)
connector.BULK_CHAR_BULK_WRITE_0_UUID = UUID(write_uuid)
connector.BULK_CHAR_READ_0_UUID       = UUID(notify_uuid)
# etc — instance attrs shadow class-level defaults
```

This means the rest of the bridge code (`ESPHomeAPIClient`, `AquaCleanClient`, frame handling) is UUID-agnostic and requires no changes for Variant A — only the connector-level UUID constants need to be set correctly.

For the HACS integration, discovered UUIDs from the config flow GATT probe step would be stored in `config_entry.data` and injected into `BluetoothLeConnector` at coordinator setup time. See CLAUDE.md section "Dynamic UUID support in HACS".

---

## The `check_gatt_services` Bug and Fix (commit 0259f88)

`tools/aquaclean-connection-test.py` `check_gatt_services()` filtered candidate services by checking whether the service UUID matched the standard Bluetooth SIG base pattern `0000xxxx-0000-1000-8000-00805f9b34fb`. Any service matching that pattern was silently skipped as a "standard Bluetooth service" with no Geberit relevance.

This caused the `0000fd48` data channel in Variant A to be silently excluded — the connection test reported "GATT profile NOT found" and exited without probing the device at all.

**Fix (commit 0259f88):** A service is now also included as a candidate if it contains at least one non-standard (vendor) characteristic UUID, regardless of the service UUID format. Since `559eb001` and `559eb002` are vendor UUIDs, `0000fd48` is correctly selected as a probe candidate.

---

## Investigation Triggers

Refer to this file when:

- A user reports `"GATT profile NOT found"` from `aquaclean-connection-test.py` but BLE connection succeeds (device is found and connectable)
- The HACS integration fails on the GATT discovery step with the same error despite a reachable device
- A BLE log or GATT dump shows `559eb` UUIDs but no `3334429d` service
- Expanding support to a new Geberit model family whose GATT layout is unknown

**First diagnostic step:** run `aquaclean-connection-test.py --dynamic-uuids` and capture its full output. The GATT table dump will show which services and characteristics are present and allow comparison against the Standard and Variant A profiles above.
