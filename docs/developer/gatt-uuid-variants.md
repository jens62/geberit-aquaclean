# GATT UUID Variants — Non-Standard Geberit BLE Profiles

**Analysis date:** 2026-04-24 (updated)
**Status:** Variant A confirmed as AquaClean Alba. iPhone BLE captures analyzed — **encrypted application-layer protocol confirmed**. Alba support requires app reverse-engineering; not feasible with current bridge approach.

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

Observed on: `E4:85:01:CD:B0:08` — **AquaClean Alba** (confirmed via [GitHub issue #17](https://github.com/jens62/geberit-aquaclean/issues/17)).

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

**What is NOT yet known for Variant A (as of 2026-04-24):**
- How to decode the encrypted data payloads — requires app reverse-engineering or a known encryption key
- Which other Geberit models (beyond the confirmed Alba) use this profile

**Confirmed for Variant A (2026-04-21):**
- `559eb001` is `[WRITE_NO_RESP]` only (GATT properties 0x04, no 0x08). Using ATT_WRITE_REQUEST caused GATT error 0x03 "Write not permitted".
- Fix: `ESPHomeAPIClient.write_gatt_char` auto-detects write type from GATT properties at connect time (commit 7433b6b). No configuration needed.

**Confirmed for Variant A (2026-04-24, from iPhone PacketLogger captures):**
- `559eb101` is **never written to** during init — the `559eb100` service is only READ (`559eb110`); it is not an initialization channel for the data service
- `559eb110` returns a static 18-byte device config: `05 06 FA 00 3E 86 05 02 95 04 03 20 01 0E 01 01 02 00` (identical across sessions)
- The Alba uses an **encrypted application-layer protocol**, completely different from the standard Geberit plaintext framing — this is why `BLEPeripheralTimeoutError` occurs: the bridge sends plaintext Geberit frames but the Alba ignores them
- The probable role mapping `559eb001→WRITE_0` and `559eb001→NOTIFY_0` is correct at the GATT level, but the frame format above GATT is different

See "Alba BLE Protocol Analysis" section below for the confirmed init sequence.

### ATT error 0x03 — diagnostic meaning

`error=3 description=Write not permitted` = **ATT_ERR_WRITE_NOT_PERMITTED**: the characteristic does not support the WRITE_REQUEST operation. This is a write-type mismatch, not a security/pairing issue. Security errors are 0x05 (insufficient authentication) and 0x08 (insufficient encryption). Error 0x03 on a `[WRITE_NO_RESP]`-only characteristic unambiguously means: use `response=False` (ATT_WRITE_COMMAND).

### Protocol probe is not read-only

`--dynamic-uuids` protocol probe runs `connect_async()` + `subscribe_notifications_async()` + `get_device_identification_async()`. All three involve writes to the write characteristic:
1. `connect_async()` sends SubscribeNotifications (4× Proc 0x01/0x13) to the write char
2. `get_device_identification_async()` sends the GetDeviceIdentification request to the write char
3. Response arrives via NOTIFY on the notify char

The `[FAIL] GATT profile` result for Variant A is **expected and correct** — `3334429d` service is absent. `--dynamic-uuids` handles it via UUID injection. It does not need to be fixed.

### Root cause of BLEPeripheralTimeoutError (confirmed 2026-04-24)

The bridge's write to `559eb001` **succeeds at the GATT layer** — the device acknowledges the write. However, the device **never responds via NOTIFY on `559eb002`** because it does not recognise the standard Geberit plaintext frame format. The Alba uses an encrypted application-layer protocol (see "Alba BLE Protocol Analysis" below). The device silently discards frames it cannot decrypt/parse, causing the bridge to time out waiting for a NOTIFY response.

This means: the GATT channel is correct (`559eb001`/`559eb002`) but the **frame format above GATT must also match** before the device responds.

---

## Alba BLE Protocol Analysis (2026-04-24)

Source: iPhone PacketLogger captures from `E4:85:01:CD:51:6B` (AquaClean Alba, issue #17).
Files: `local-assets/Bluetooth-Logs/johannes-schliephake/connect.txt` and `connect+actions.txt`.

### Init sequence (confirmed, deterministic across sessions)

| Step | Direction | Handle | Data | Notes |
|------|-----------|--------|------|-------|
| 1 | Device → Phone | 0x001E | Write Request (rejected) | Device attempts unsolicited write; phone rejects "Write Not Permitted" |
| 2 | — | — | MTU negotiation | Phone proposes 527, device accepts 23 |
| 3 | — | — | GATT discovery | Finds `559eb100` (0x000C–0x0010) and `0000fd48` (0x001C–0x0021) services |
| 4 | Phone → Device | 0x0004 (CCCD) | `02 00` | Enable indication on Service Changed |
| 5 | Phone → Device | 0x0021 (CCCD) | `01 00` | Enable notification on `559eb002` |
| 6 | Phone → Device | 0x0010 | READ | Read `559eb110` |
| 6r | Device → Phone | — | `05 06 FA 00 3E 86 05 02 95 04 03 20 01 0E 01 01 02 00` | Static 18-byte device config (identical across sessions) |
| 7 | Phone → Device | 0x001F (`559eb001`) | `00 04 2F F5 D9 00` | Handshake frame 1 |
| 7r | Device → Phone | Notify | `00 04 63 9D 51 00` | Handshake response 1 |
| 8 | Phone → Device | 0x001F | `00 01 01 01 01 01 00` | Handshake frame 2 |
| 8r | Device → Phone | Notify | `00 03 20 01 02 05 01 01 04 01 F8 1E 00` | Session parameters |
| 8r2 | Device → Phone | Notify | `00 04 21 8B 30 00` | ACK / session handle |
| 9 | Phone → Device | 0x001F | `00 04 21 8B 30 00` + `00 04 22 10 02 01 00` | Echo handle + second frame |
| 9r | Device → Phone | Notify | Large encrypted response (fragmented) | Likely device identification |
| 10 | Phone → Device | 0x001F | `00 04 41 8D 53 00` | ACK segment 1 |
| … | — | — | Interleaved ACKs + encrypted data | Steady-state encrypted exchange |

### Frame format

**Cleartext control frames** (only during handshake):
```
00 04 XX XX XX 00
```
Where `XX XX XX` is a 3-byte sequence number. The ACK sequence increments the high byte by 0x20 per frame:
`21 8B 30` → `41 8D 53` → `61 8F 72` → `81 81 95` → `A1 83 B4` → `C1 85 D7` → `E1 87 F6` → `01 89 11` (wraps)

**Encrypted data frames** (steady state):
```
00 0D XX 20 [encrypted payload] 00
00 0E XX 20 [encrypted payload] 00
```
Payload bytes differ between sessions — cannot decode without the encryption scheme.

**Handshake frame 2 response** `00 03 20 01 02 05 01 01 04 01 F8 1E 00`:
- Byte 0: `00` (frame type prefix)
- Byte 1: `03` (frame type / length?)
- Bytes 2–3: `20 01` (session parameter — possibly protocol version or session ID)
- Byte 4: `02` (unknown)
- Byte 5: `05` (unknown — possibly MTU negotiation result)
- Bytes 6–12: session parameters / capabilities

### What `559eb101` is for

`559eb101` (WRITE_NO_RESP in `559eb100` service) is **never written to** during any observed session, including ones with settings changes. Its purpose is unknown — possibly factory/firmware use only. It is **not** needed to initialise the data channel. The `559eb100` service is effectively read-only from the app's perspective (only `559eb110` READ is used).

### What the captures do NOT reveal

- Encryption key or scheme — payloads are opaque; different every session
- Whether there is a BLE-level pairing/bonding step that provides the key (not visible in HCI logs if pre-established)
- Mapping of encrypted data to device state or commands

### What would be needed to proceed

To support the Alba in the bridge, one of the following is required:

1. **Encryption key extraction** — requires reverse-engineering the Geberit Home iOS app (`.ipa` binary analysis) or the firmware on the device itself
2. **Protocol documentation** from Geberit (unlikely to be available)
3. **Man-in-the-middle BLE proxy** that relays known-plaintext commands and captures the corresponding encrypted output — very high effort

Without one of the above, Alba support is not feasible with the current bridge approach. The captures are sufficient to confirm this conclusively.

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
