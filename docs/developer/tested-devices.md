# Tested Devices — Compatibility Matrix

This document lists every Geberit AquaClean device that has been confirmed to work
(or not work) with this bridge, along with the GATT profile variant used.

---

## Confirmed Working

| Device | Serial / Prefix | GATT Profile | BLE MAC | Tested By | Notes |
|--------|----------------|--------------|---------|-----------|-------|
| AquaClean Mera Comfort | HB2304EU298413 | Standard (`3334429d`) | `38:AB:41:2A:0D:67` | Developer (primary device) | All features tested; BLE logs in `local-assets/Bluetooth-Logs/` |

---

## Partial / In Progress

| Device | Serial / Prefix | GATT Profile | BLE MAC | GitHub Issue | Status |
|--------|----------------|--------------|---------|--------------|--------|
| AquaClean Alba | Unknown | Variant A (`0000fd48` + `559eb` chars) | `E4:85:01:CD:B0:08` | [#17](https://github.com/jens62/geberit-aquaclean/issues/17) | GATT discovery confirmed; write-type fix applied (commit 7433b6b); **protocol probe fails** (`BLEPeripheralTimeoutError` — writes succeed, no notify response). BLE traffic capture from Geberit Home App needed. |
| AquaClean Alba | SB2509EU177754, SAP 146.350.01.x | Variant A (`0000fd48` + `559eb` chars) | `E4:85:01:CD:51:6B` | BLE captures from Johannes Schliephake | Firmware RS3.0 TS89. GetDeviceIdentification response is **encrypted** (session-specific key, 32-byte XOR cipher). Two sessions captured; known-plaintext attack attempted — no repeating keystream found. Session key derived from internal counter not visible in BLE. See `docs/developer/alba-ble-encryption.md`. |
| AquaClean Alba | DIS serial `93136`, SAP `828.860.00.A` | Variant A (`0000fd48` + `559eb` chars) | `E4:85:01:CD:6B:04` | pcapng + standalone bridge log from kstr | Firmware `RS03TS89`, sw `1.14.1 1.2.0`, hw `00`. Full working app session captured. Init sequence byte-for-byte identical to Johannes's device. GetDeviceIdentification also encrypted (same scheme). Cross-device XOR analysis suggests device-specific key. BLE DIS values confirmed from `aquaclean2.log`. See `docs/developer/alba-ble-encryption.md`. |

---

## BLE DIS identity vs. Geberit application-layer identity

Geberit devices expose two separate sets of identification data:

| Source | How to read | Fields | Example (Alba) |
|--------|-------------|--------|----------------|
| **BLE DIS** (`0x180a`) | `read_gatt_char` on standard BLE characteristics | `model_number` = SAP article number, `serial_number` = hardware/PCB serial | model `828.860.00.A`, serial `93136` |
| **Geberit proc `0x82`** (`GetDeviceIdentification`) | Proprietary Geberit protocol over GATT data channel | product name (`AcAlba`), full Geberit serial (`SB2603EU208023`), SAP | only reachable on Standard profile |

The DIS `model_number` is **not** the Geberit Artikelnummer shown in the app (e.g. `146.350.01.x`
for the Alba). It is an unknown internal identifier — possibly a PCB/board model or BLE module
number. Its exact meaning is unconfirmed; a comparison against the Mera Comfort DIS values is needed.

The product name (`AcAlba`) and full serial (`SB...`) only come from proc `0x82`
(GetDeviceIdentification), which requires the Standard GATT profile.

On Variant A devices the bridge falls back to BLE DIS for identification — this is why the
unsupported-device error shows `828.860.00.A (93136)` rather than `AcAlba (SB...)`.

---

## Notes on GATT Profile Variants

See `docs/developer/gatt-uuid-variants.md` for full GATT table details and UUID mappings.

| Profile | Service UUID | Known devices |
|---------|-------------|---------------|
| Standard | `3334429d-90f3-4c41-a02d-5cb3a03e0000` | AquaClean Mera Comfort (confirmed) |
| Variant A | `0000fd48-0000-1000-8000-00805f9b34fb` | AquaClean Alba (confirmed via issue #17) |

---

## Adding a New Device

If you have a device not listed above and want to test compatibility:

1. Run `tools/aquaclean-connection-test.py --dynamic-uuids` and paste the full output into a GitHub issue
2. The GATT table in the output will identify which profile variant your device uses
3. If the connection test passes, the bridge will work; if it fails, the output contains the exact failure point

The `--dynamic-uuids` flag handles Variant A automatically if the frame protocol is the same.
For devices where the protocol probe also fails, a BLE traffic capture from the Geberit Home App is needed — see `docs/developer/ble-traffic-capture.md`.
